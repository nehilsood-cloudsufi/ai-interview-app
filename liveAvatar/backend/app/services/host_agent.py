"""Host agent core: a deterministic interview state machine around one
structured Gemini turn.

Per user utterance, `handle_turn` makes a single call to Gemini's
OpenAI-compatible chat endpoint (same raw-httpx pattern as
`summary_service`) asking for JSON `{"reply", "answer_complete",
"profile_updates"}`. The LLM only phrases the reply, judges whether the
current question is fully answered, and reports any vendor profile details
just stated/corrected - all state mutation (appending turns, advancing
`current_node_id` along the fixed linear script, follow-up accounting,
merging profile_updates into `state.vendor_profile`) is done here in code.

Answer-text collection: `state.followup_count` counts the follow-up rounds
already exchanged for the current question, and each round appended exactly
one candidate turn and one interviewer turn to `state.turns`. So the user's
prior partial answers for the current question are the candidate-role
entries among the last `2 * followup_count` turns; when a question
completes, `answer_text` is those texts plus the new utterance.

On any HTTP or parse failure the turn soft-fails: a generic "say that
again" reply is returned and the state is left completely unchanged.
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.config import settings
from app.models import TranscriptTurn
from app.services import gemini_client
from app.services.interview_config import QuestionNode, RubricCategory
from app.services.interview_state import InterviewState, VendorProfile
from app.services.llm_json import parse_llm_json
from app.services.reply_stream import ReplyStreamExtractor

logger = logging.getLogger(__name__)

END_NODE_ID = "END"

_ROLE_LABELS = {"interviewer": "Interviewer", "candidate": "Candidate"}
# How many trailing entries of state.turns are replayed to Gemini as context.
_TRANSCRIPT_WINDOW = 10

# HeyGen's LiveKit agent gives our gateway a 10s read timeout (see
# docs/llm-gateway-notes.md); Gemini must answer well inside that so a slow
# call becomes our fallback reply instead of HeyGen timing out to silence.
_GEMINI_TIMEOUT_SECONDS = 8.0
# Bounds the spoken reply. Gemini counts thinking tokens against this cap
# (~150 at low effort, measured live), so keep enough headroom that the JSON
# never truncates mid-object - a truncated turn is worse than a long one.
# 800 (was 500): replies now carry acknowledgment + the next question, and a
# 500-cap turn was observed truncating mid-JSON live on 2026-07-20.
_MAX_TOKENS = 800

# Strict structured output: the compat endpoint constrains decoding to this
# schema, which (with parse_llm_json as the belt) prevents the malformed-JSON
# turns observed in the 2026-07-18 live test.
# `reply` must stay the FIRST property - the streaming extractor
# (reply_stream.ReplyStreamExtractor) depends on it being emitted first.
# `profile_updates` is required (with all four of ITS properties required
# too) because the Gemini compat endpoint's strict mode rejects a schema that
# omits a property from `required`; null is how the LLM says "nothing new".
_TURN_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "answer_complete": {"type": "boolean"},
        "profile_updates": {
            "type": "object",
            "properties": {
                "company_name": {"type": ["string", "null"]},
                "website": {"type": ["string", "null"]},
                "contact_name": {"type": ["string", "null"]},
                "contact_role": {"type": ["string", "null"]},
            },
            "required": ["company_name", "website", "contact_name", "contact_role"],
        },
    },
    "required": ["reply", "answer_complete", "profile_updates"],
}

# Keys of profile_updates / VendorProfile fields it may overwrite.
_PROFILE_FIELDS = ("company_name", "website", "contact_name", "contact_role")


@dataclass
class TurnResult:
    reply: str  # what the avatar says next
    answer_complete: bool  # True -> current question answered; state advanced
    completed_question: QuestionNode | None  # set when answer_complete (Appraiser hook)
    answer_text: str  # user's full answer for the completed question ("" if not complete)


def _render_transcript(turns: list[TranscriptTurn]) -> str:
    lines = []
    for turn in turns:
        label = _ROLE_LABELS.get(turn.role, turn.role.title())
        text = turn.text.strip()
        if text:
            lines.append(f"{label}: {text}")
    return "\n".join(lines)


_NOT_CAPTURED = "(not captured yet)"


def _display(value: str | None) -> str:
    return value.strip() if value and value.strip() else _NOT_CAPTURED


def _render_system_content(
    state: InterviewState, node: QuestionNode, questionnaire: dict[str, QuestionNode]
) -> str:
    profile = state.vendor_profile

    lines = [
        settings.host_system_prompt,
        "",
        "Vendor profile:",
        f"- Company: {_display(profile.company_name)}",
        f"- Website: {_display(profile.website)}",
        f"- Contact name: {_display(profile.contact_name)}",
        f"- Contact role: {_display(profile.contact_role)}",
        "- Whenever the vendor states or corrects any of these details, "
        "report them in profile_updates; otherwise return null for all four fields.",
    ]

    next_node = questionnaire.get(node.next)  # None only for the literal END
    if next_node is None:
        next_line = "- no further questions - thank them and close the interview."
    else:
        next_line = f"- {next_node.ask}"

    lines += [
        "",
        "Current question:",
        f"- Topic: {node.topic}",
        f"- Ask: {node.ask}",
        "",
        # The LLM never advances the script itself, but it must SPEAK the next
        # question in the same reply that closes the current one - HeyGen only
        # calls us when the vendor talks, so a reply that ends on a bare
        # acknowledgment leaves the avatar waiting in silence (observed live
        # 2026-07-20).
        "If the answer is complete, the next question to ask in the same reply:",
        next_line,
    ]

    return "\n".join(lines)


def _render_user_content(state: InterviewState, user_text: str) -> str:
    transcript = _render_transcript(state.turns[-_TRANSCRIPT_WINDOW:])
    latest = f"{_ROLE_LABELS['candidate']}: {user_text.strip()}"
    return f"{transcript}\n{latest}" if transcript else latest


def _build_payload(
    state: InterviewState,
    node: QuestionNode,
    user_text: str,
    questionnaire: dict[str, QuestionNode],
) -> dict:
    """The per-turn Gemini request, shared by the buffered and streaming paths.
    `reply` is first in `_TURN_SCHEMA` so the streaming path can speak it before
    the trailing routing fields arrive."""
    return {
        "model": settings.gemini_model,
        "messages": [
            {"role": "system", "content": _render_system_content(state, node, questionnaire)},
            {"role": "user", "content": _render_user_content(state, user_text)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "host_turn", "strict": True, "schema": _TURN_SCHEMA},
        },
        "reasoning_effort": "low",
        "max_tokens": _MAX_TOKENS,
    }


async def _call_gemini(
    state: InterviewState,
    node: QuestionNode,
    user_text: str,
    questionnaire: dict[str, QuestionNode],
) -> dict:
    payload = _build_payload(state, node, user_text, questionnaire)

    # Parse failures get one retry (fresh sample, fast); HTTP failures don't -
    # a Gemini outage should fall back immediately, not double the wait inside
    # HeyGen's timeout window. The single exception is gemini_client's
    # model-fallback retry (a model-not-found 404 returns in <1s).
    for attempt in (1, 2):
        data = await gemini_client.chat_completion(
            payload, timeout=_GEMINI_TIMEOUT_SECONDS, fallback_model=settings.gemini_model_fallback
        )
        content = data["choices"][0]["message"]["content"]
        try:
            return parse_llm_json(content)
        except ValueError:
            if attempt == 2:
                raise
            logger.warning(
                "Unparsable host turn for interview %s at node %s; retrying once. Content: %.500r",
                state.interview_id,
                state.current_node_id,
                content,
            )
    raise AssertionError("unreachable")


async def handle_turn(
    state: InterviewState,
    user_text: str,
    questionnaire: dict[str, QuestionNode],
    rubric: dict[str, RubricCategory] | None,
) -> TurnResult:
    """Process one user utterance: one Gemini call, then code-driven state
    mutation. `rubric` is accepted for interface parity with the Appraiser
    flow; the Host itself does not score answers."""
    if state.current_node_id == END_NODE_ID:
        return TurnResult(
            reply=settings.host_closing_reply,
            answer_complete=False,
            completed_question=None,
            answer_text="",
        )

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured; cannot run the host agent.")

    node = questionnaire[state.current_node_id]

    try:
        parsed = await _call_gemini(state, node, user_text, questionnaire)
        reply = str(parsed["reply"])
        answer_complete = bool(parsed.get("answer_complete"))
        profile_updates = parsed.get("profile_updates")
    except Exception:
        logger.warning(
            "Host turn failed for interview %s at node %s; returning fallback reply.",
            state.interview_id,
            state.current_node_id,
            exc_info=True,
        )
        return TurnResult(
            reply=settings.host_fallback_reply,
            answer_complete=False,
            completed_question=None,
            answer_text="",
        )

    return _apply_outcome(state, node, user_text, reply, answer_complete, profile_updates)


def _merge_profile_updates(profile: VendorProfile, updates: dict | None) -> None:
    """Overwrite only the fields the vendor actually gave a new value for -
    null, missing, or whitespace-only entries leave the existing value alone.
    Runs on EVERY turn (not just the onboarding nodes) so a late mention or
    correction anywhere in the interview still sticks."""
    if not updates:
        return
    for field_name in _PROFILE_FIELDS:
        value = updates.get(field_name)
        if isinstance(value, str) and value.strip():
            setattr(profile, field_name, value.strip())


def _apply_outcome(
    state: InterviewState,
    node: QuestionNode,
    user_text: str,
    reply: str,
    answer_complete: bool,
    profile_updates: dict | None,
) -> TurnResult:
    """Deterministic state mutation once the reply and judgement are in hand.
    Shared by `handle_turn` and `stream_turn` so both drive the state machine
    identically. Only reached on the success path - soft-fail returns before
    ever calling this, so a failed turn never merges a profile update."""
    _merge_profile_updates(state.vendor_profile, profile_updates)

    # Candidate texts already given for this node (see module docstring),
    # captured before the new turns are appended.
    prior_answer_texts = [
        turn.text
        for turn in state.turns[max(0, len(state.turns) - 2 * state.followup_count) :]
        if turn.role == "candidate"
    ]

    state.turns.append(TranscriptTurn(role="candidate", text=user_text))
    state.turns.append(TranscriptTurn(role="interviewer", text=reply))

    if not answer_complete:
        state.followup_count += 1
        if state.followup_count <= node.max_followups:
            return TurnResult(
                reply=reply,
                answer_complete=False,
                completed_question=None,
                answer_text="",
            )
        # Follow-up budget exhausted: force-advance as if complete, still
        # speaking the LLM's reply.

    state.current_node_id = node.next
    state.followup_count = 0
    return TurnResult(
        reply=reply,
        answer_complete=True,
        completed_question=node,
        answer_text="\n".join([*prior_answer_texts, user_text]),
    )


@dataclass
class StreamedTurn:
    """Holder for the final outcome of a streaming turn. `stream_turn` yields
    reply text and, once the object is complete, sets `result` (a `TurnResult`)
    so the caller can log the same fields the buffered path reports."""

    result: TurnResult | None = None


async def stream_turn(
    state: InterviewState,
    user_text: str,
    questionnaire: dict[str, QuestionNode],
    rubric: dict[str, RubricCategory] | None,
    outcome: StreamedTurn,
) -> AsyncIterator[str]:
    """Streaming counterpart to `handle_turn`: yields the spoken reply in
    fragments as Gemini emits them, then applies the same code-driven state
    mutation from the trailing routing fields.

    Soft-fail contract mirrors `handle_turn`: if the call fails before any
    reply character is spoken, the canned fallback reply is yielded and state
    is left unchanged. If it fails after the reply is (partly) spoken - e.g.
    the trailing JSON is malformed - the spoken text stands and state is left
    unchanged rather than half-advanced."""
    if state.current_node_id == END_NODE_ID:
        outcome.result = TurnResult(
            reply=settings.host_closing_reply,
            answer_complete=False,
            completed_question=None,
            answer_text="",
        )
        yield settings.host_closing_reply
        return

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured; cannot run the host agent.")

    node = questionnaire[state.current_node_id]
    payload = _build_payload(state, node, user_text, questionnaire)
    extractor = ReplyStreamExtractor()

    try:
        async for delta in gemini_client.stream_chat_completion(
            payload, timeout=_GEMINI_TIMEOUT_SECONDS, fallback_model=settings.gemini_model_fallback
        ):
            chunk = extractor.feed(delta)
            if chunk:
                yield chunk
        parsed, remaining = extractor.finalize()
    except Exception:
        logger.warning(
            "Streaming host turn failed for interview %s at node %s.",
            state.interview_id,
            state.current_node_id,
            exc_info=True,
        )
        if not extractor.emitted:
            outcome.result = TurnResult(
                reply=settings.host_fallback_reply,
                answer_complete=False,
                completed_question=None,
                answer_text="",
            )
            yield settings.host_fallback_reply
        else:
            outcome.result = TurnResult(
                reply=extractor.emitted,
                answer_complete=False,
                completed_question=None,
                answer_text="",
            )
        return

    if remaining:
        yield remaining
    outcome.result = _apply_outcome(
        state,
        node,
        user_text,
        extractor.emitted + remaining,
        bool(parsed.get("answer_complete")),
        parsed.get("profile_updates"),
    )
