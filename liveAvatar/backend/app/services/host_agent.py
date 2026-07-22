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
Fields the vendor has manually corrected via `PATCH
/api/interview/{id}/profile` (tracked in `state.manually_edited_fields`) are
permanently locked against this merge - manual edits always win, though the
vendor can always re-edit that field manually again.

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
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.config import settings
from app.models import TranscriptTurn
from app.services import gemini_client
from app.services.interview_config import QuestionNode, RubricCategory
from app.services.interview_state import InterviewState, VendorProfile
from app.services.llm_json import parse_llm_json
from app.services.reply_stream import ReplyStreamExtractor
from app.services.transcript_render import ROLE_LABELS, render_transcript

logger = logging.getLogger(__name__)

END_NODE_ID = "END"

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
                "contact_name": {"type": ["string", "null"]},
                "contact_role": {"type": ["string", "null"]},
            },
            "required": ["company_name", "contact_name", "contact_role"],
        },
    },
    "required": ["reply", "answer_complete", "profile_updates"],
}

# Keys of profile_updates / VendorProfile fields it may overwrite.
_PROFILE_FIELDS = ("company_name", "contact_name", "contact_role")


@dataclass
class TurnResult:
    """The outcome of one processed utterance, returned by `handle_turn` (and
    stashed on `StreamedTurn.result` by the streaming path). It carries both
    what the avatar should say next and the routing decision the code made:
    whether the current question is now satisfied and, when it is, which
    `QuestionNode` just closed plus the vendor's full answer text for it
    (this utterance joined with any earlier partial answers to the same
    question). While the answer is still incomplete `completed_question` is
    None and `answer_text` is empty. The completed-question / answer-text pair
    is the hook a per-answer Evaluator would consume; the live flow does not
    score here."""

    reply: str  # what the avatar says next
    answer_complete: bool  # True -> current question answered; state advanced
    completed_question: QuestionNode | None  # set when answer_complete (Evaluator hook)
    answer_text: str  # user's full answer for the completed question ("" if not complete)


def _seconds_remaining(state: InterviewState, mode: Literal["avatar", "chat"]) -> float | None:
    """Session-clock bookkeeping for time-aware pacing. Returns the seconds
    left on the interview's session clock, stamping `state.first_turn_at` on
    the first clocked turn. Returns None whenever there is no clock to pace
    against: chat mode (no HeyGen session, no cap) or an interview without
    `max_session_seconds` (dev tier - the ~1-min sandbox cap is too short to
    pace, sessions there just end)."""
    if mode != "avatar" or state.max_session_seconds is None:
        return None
    now = datetime.now(timezone.utc)
    if state.first_turn_at is None:
        state.first_turn_at = now
    return state.max_session_seconds - (now - state.first_turn_at).total_seconds()


def _wrapup_turn(state: InterviewState, user_text: str) -> TurnResult:
    """Deterministic time's-up closing: no LLM call (no latency/failure risk
    in the final seconds), the remaining script is skipped, and the exchange
    still lands in the transcript for the Evaluator."""
    state.turns.append(TranscriptTurn(role="candidate", text=user_text))
    state.turns.append(TranscriptTurn(role="interviewer", text=settings.host_timeup_reply))
    state.current_node_id = END_NODE_ID
    state.followup_count = 0
    return TurnResult(
        reply=settings.host_timeup_reply,
        answer_complete=False,
        completed_question=None,
        answer_text="",
    )


_NOT_CAPTURED = "(not captured yet)"


def _display(value: str | None) -> str:
    """Render a vendor-profile field for the system prompt, substituting a
    visible "(not captured yet)" marker when the value is missing or
    whitespace-only - so the LLM can see which details it still needs to
    gather from the conversation."""
    return value.strip() if value and value.strip() else _NOT_CAPTURED


def _render_system_content(
    state: InterviewState,
    node: QuestionNode,
    questionnaire: dict[str, QuestionNode],
    mode: Literal["avatar", "chat"] = "avatar",
    time_pressured: bool = False,
) -> str:
    """Assemble the system message for one Host turn: the base host prompt,
    the vendor profile captured so far (each field via `_display`, plus the
    standing instruction to report any newly stated or corrected detail in
    profile_updates), the current question's topic and ask, and the exact next
    question to speak once this one is answered. The LLM never advances the
    script itself, but it must voice that next question in the same reply that
    closes the current one (see the inline note - a bare acknowledgment leaves
    the avatar silent). Appends `settings.host_avatar_mode_prompt` when
    `mode="avatar"` (VAD-fragment awareness - see that setting's comment),
    `settings.host_chat_mode_prompt` when `mode="chat"`, and
    `settings.host_time_pressure_prompt` when `time_pressured` is set."""
    profile = state.vendor_profile

    lines = [
        settings.host_system_prompt,
        "",
        "Vendor profile:",
        f"- Company: {_display(profile.company_name)}",
        f"- Contact name: {_display(profile.contact_name)}",
        f"- Contact role: {_display(profile.contact_role)}",
        "- Whenever the vendor states or corrects any of these details, "
        "report them in profile_updates; otherwise return null for all three fields.",
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

    if mode == "avatar":
        lines += ["", settings.host_avatar_mode_prompt]

    if mode == "chat":
        lines += ["", settings.host_chat_mode_prompt]

    if time_pressured:
        lines += ["", settings.host_time_pressure_prompt]

    return "\n".join(lines)


def _render_user_content(state: InterviewState, user_text: str) -> str:
    """Assemble the user message: the last `_TRANSCRIPT_WINDOW` turns rendered
    as a transcript, followed by the new candidate utterance. Only the recent
    window is replayed (not the whole history) to keep the per-turn call cheap
    and comfortably inside HeyGen's timeout."""
    transcript = render_transcript(state.turns[-_TRANSCRIPT_WINDOW:])
    latest = f"{ROLE_LABELS['candidate']}: {user_text.strip()}"
    return f"{transcript}\n{latest}" if transcript else latest


def _build_payload(
    state: InterviewState,
    node: QuestionNode,
    user_text: str,
    questionnaire: dict[str, QuestionNode],
    mode: Literal["avatar", "chat"] = "avatar",
    time_pressured: bool = False,
) -> dict:
    """The per-turn Gemini request, shared by the buffered and streaming paths.
    `reply` is first in `_TURN_SCHEMA` so the streaming path can speak it before
    the trailing routing fields arrive."""
    return {
        "model": settings.gemini_model,
        "messages": [
            {
                "role": "system",
                "content": _render_system_content(state, node, questionnaire, mode, time_pressured),
            },
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
    mode: Literal["avatar", "chat"] = "avatar",
    time_pressured: bool = False,
) -> dict:
    """Make the single per-turn Gemini call and return its parsed JSON turn.
    A parse failure is retried exactly once with a fresh sample (a cheap, fast
    reroll); HTTP failures are deliberately not retried here, so a Gemini
    outage falls back immediately rather than doubling the wait inside
    HeyGen's timeout window (gemini_client's own model-not-found fallback is
    the one exception, and returns in under a second). Raises if the second
    parse also fails or on any HTTP error, leaving the soft-fail to the
    caller."""
    payload = _build_payload(state, node, user_text, questionnaire, mode, time_pressured)

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
    mode: Literal["avatar", "chat"] = "avatar",
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
) -> TurnResult | None:
    """Process one user utterance: one Gemini call, then code-driven state
    mutation. `rubric` is accepted for interface parity with the Evaluator
    flow; the Host itself does not score answers. `mode` selects which
    frontend is driving the turn: "avatar" (HeyGen, spoken - the default)
    appends settings.host_avatar_mode_prompt so VAD fragments of an unfinished
    answer aren't judged complete; "chat" appends
    settings.host_chat_mode_prompt so terse typed answers aren't treated as
    incomplete.

    Turns for the same interview are serialized on `state.turn_lock`: HeyGen's
    VAD can fire overlapping gateway calls for one flowing answer, and two
    concurrent turns reading the same `current_node_id` would double-advance
    the script (seen live 2026-07-22).

    `is_cancelled` (the gateway passes `request.is_disconnected`) makes
    superseded turns invisible: HeyGen cancels its in-flight request whenever
    a new speech fragment arrives, so a turn whose request is already gone is
    skipped before the Gemini call - and its outcome is discarded before any
    state mutation if the cancellation lands mid-call. Returns None in both
    cases (nobody is listening for the reply); the interview state then only
    ever advances on turns the vendor could actually hear."""
    async with state.turn_lock:
        if is_cancelled is not None and await is_cancelled():
            logger.info(
                "Turn superseded before processing for interview %s at node %s; skipping.",
                state.interview_id,
                state.current_node_id,
            )
            return None
        return await _handle_turn(state, user_text, questionnaire, rubric, mode, is_cancelled)


async def _handle_turn(
    state: InterviewState,
    user_text: str,
    questionnaire: dict[str, QuestionNode],
    rubric: dict[str, RubricCategory] | None,
    mode: Literal["avatar", "chat"] = "avatar",
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
) -> TurnResult | None:
    """`handle_turn`'s body, running with `state.turn_lock` already held."""
    if state.current_node_id == END_NODE_ID:
        return TurnResult(
            reply=settings.host_closing_reply,
            answer_complete=False,
            completed_question=None,
            answer_text="",
        )

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured; cannot run the host agent.")

    remaining = _seconds_remaining(state, mode)
    if remaining is not None and remaining <= settings.host_wrapup_seconds:
        return _wrapup_turn(state, user_text)
    time_pressured = remaining is not None and remaining <= settings.host_time_pressure_seconds

    node = questionnaire[state.current_node_id]

    try:
        parsed = await _call_gemini(state, node, user_text, questionnaire, mode, time_pressured)
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

    # A new speech fragment may have superseded this turn while Gemini was
    # answering - HeyGen has already dropped the connection, so the reply will
    # never be spoken. Discard the outcome before ANY state mutation: if the
    # vendor never heard it, it didn't happen.
    if is_cancelled is not None and await is_cancelled():
        logger.info(
            "Turn superseded during Gemini call for interview %s at node %s; discarding outcome.",
            state.interview_id,
            state.current_node_id,
        )
        return None

    # Under time pressure the script must keep moving: even if the LLM judged
    # the answer incomplete, accept it and advance rather than spend the
    # remaining seconds on follow-ups.
    return _apply_outcome(
        state, node, user_text, reply, answer_complete or time_pressured, profile_updates
    )


def _merge_profile_updates(profile: VendorProfile, updates: dict | None, locked: set[str]) -> None:
    """Overwrite only the fields the vendor actually gave a new value for -
    null, missing, or whitespace-only entries leave the existing value alone.
    Runs on EVERY turn (not just the onboarding nodes) so a late mention or
    correction anywhere in the interview still sticks. Fields in `locked`
    (manually corrected via PATCH /api/interview/{id}/profile) are skipped
    entirely - a manual edit always wins over the LLM's profile_updates,
    though the vendor can still re-edit that field manually at any time."""
    if not updates:
        return
    for field_name in _PROFILE_FIELDS:
        if field_name in locked:
            continue
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
    _merge_profile_updates(state.vendor_profile, profile_updates, state.manually_edited_fields)

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
    mode: Literal["avatar", "chat"] = "avatar",
) -> AsyncIterator[str]:
    """Streaming counterpart to `handle_turn`: yields the spoken reply in
    fragments as Gemini emits them, then applies the same code-driven state
    mutation from the trailing routing fields. `mode` behaves identically to
    `handle_turn`'s.

    Soft-fail contract mirrors `handle_turn`: if the call fails before any
    reply character is spoken, the canned fallback reply is yielded and state
    is left unchanged. If it fails after the reply is (partly) spoken - e.g.
    the trailing JSON is malformed - the spoken text stands and state is left
    unchanged rather than half-advanced.

    Like `handle_turn`, turns for the same interview serialize on
    `state.turn_lock` - held for the whole stream, so an overlapping gateway
    call waits out the in-flight turn instead of racing its state mutation."""
    async with state.turn_lock:
        async for text in _stream_turn(state, user_text, questionnaire, rubric, outcome, mode):
            yield text


async def _stream_turn(
    state: InterviewState,
    user_text: str,
    questionnaire: dict[str, QuestionNode],
    rubric: dict[str, RubricCategory] | None,
    outcome: StreamedTurn,
    mode: Literal["avatar", "chat"] = "avatar",
) -> AsyncIterator[str]:
    """`stream_turn`'s body, running with `state.turn_lock` already held."""
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

    remaining = _seconds_remaining(state, mode)
    if remaining is not None and remaining <= settings.host_wrapup_seconds:
        outcome.result = _wrapup_turn(state, user_text)
        yield settings.host_timeup_reply
        return
    time_pressured = remaining is not None and remaining <= settings.host_time_pressure_seconds

    node = questionnaire[state.current_node_id]
    payload = _build_payload(state, node, user_text, questionnaire, mode, time_pressured)
    extractor = ReplyStreamExtractor()

    try:
        async for delta in gemini_client.stream_chat_completion(
            payload, timeout=_GEMINI_TIMEOUT_SECONDS, fallback_model=settings.gemini_model_fallback
        ):
            chunk = extractor.feed(delta)
            if chunk:
                yield chunk
        parsed, remainder = extractor.finalize()
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

    if remainder:
        yield remainder
    outcome.result = _apply_outcome(
        state,
        node,
        user_text,
        extractor.emitted + remainder,
        bool(parsed.get("answer_complete")) or time_pressured,
        parsed.get("profile_updates"),
    )
