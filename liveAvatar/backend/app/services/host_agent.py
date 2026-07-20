"""Host agent core: a deterministic interview state machine around one
structured Gemini turn.

Per user utterance, `handle_turn` makes a single call to Gemini's
OpenAI-compatible chat endpoint (same raw-httpx pattern as
`summary_service`) asking for JSON `{"reply", "answer_complete",
"branch_signal"}`. The LLM only phrases the reply and reports its
judgement - all state mutation (appending turns, resolving branches,
advancing `current_node_id`, follow-up accounting) is done here in code.

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
from dataclasses import dataclass

from app.config import settings
from app.models import TranscriptTurn
from app.services import gemini_client
from app.services.interview_config import QuestionNode, RubricCategory
from app.services.interview_state import InterviewState
from app.services.llm_json import parse_llm_json

logger = logging.getLogger(__name__)

END_NODE_ID = "END"
DEFAULT_SIGNAL = "default"

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
_TURN_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "answer_complete": {"type": "boolean"},
        "branch_signal": {"type": "string"},
    },
    "required": ["reply", "answer_complete", "branch_signal"],
}


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


def _render_system_content(
    state: InterviewState, node: QuestionNode, questionnaire: dict[str, QuestionNode]
) -> str:
    profile = state.vendor_profile
    contact = profile.contact_name
    if profile.contact_role:
        contact += f" ({profile.contact_role})"

    lines = [
        settings.host_system_prompt,
        "",
        "Vendor profile:",
        f"- Company: {profile.company_name}",
        f"- Contact: {contact}",
    ]
    if profile.website:
        lines.append(f"- Website: {profile.website}")

    signals = ", ".join(branch.signal for branch in node.branches)
    lines += [
        "",
        "Current question:",
        f"- Topic: {node.topic}",
        f"- Ask: {node.ask}",
        f"- Allowed branch signals: {signals}",
        "",
        # The LLM never mutates the tree, but it must SPEAK the next question
        # in the same reply that closes the current one - HeyGen only calls us
        # when the vendor talks, so a reply that ends on a bare acknowledgment
        # leaves the avatar waiting in silence (observed live 2026-07-20).
        "If the answer is complete, the next question by branch signal:",
    ]
    for branch in node.branches:
        next_node = questionnaire.get(branch.next)
        if next_node is None:  # END: questionnaire validation guarantees only END is absent
            lines.append(f"- {branch.signal}: no further questions - thank them and close the interview.")
        else:
            lines.append(f"- {branch.signal}: {next_node.ask}")

    if state.scout_findings:
        lines += ["", "Known vendor intel:"]
        for finding in state.scout_findings:
            bullet = f"- {finding.topic}: {finding.summary}"
            if finding.source_url:
                bullet += f" (source: {finding.source_url})"
            lines.append(bullet)

    return "\n".join(lines)


def _render_user_content(state: InterviewState, user_text: str) -> str:
    transcript = _render_transcript(state.turns[-_TRANSCRIPT_WINDOW:])
    latest = f"{_ROLE_LABELS['candidate']}: {user_text.strip()}"
    return f"{transcript}\n{latest}" if transcript else latest


def _resolve_branch(node: QuestionNode, signal: str | None) -> str:
    for branch in node.branches:
        if branch.signal == signal:
            return branch.next
    for branch in node.branches:
        if branch.signal == DEFAULT_SIGNAL:
            return branch.next
    # Questionnaire validation guarantees a node without a default branch has
    # every branch pointing at END.
    return node.branches[0].next


async def _call_gemini(
    state: InterviewState,
    node: QuestionNode,
    user_text: str,
    questionnaire: dict[str, QuestionNode],
) -> dict:
    payload = {
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
        branch_signal = parsed.get("branch_signal")
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
        # Follow-up budget exhausted: force-advance as if complete, via the
        # default branch, still speaking the LLM's reply.
        branch_signal = DEFAULT_SIGNAL

    state.current_node_id = _resolve_branch(node, branch_signal)
    state.followup_count = 0
    return TurnResult(
        reply=reply,
        answer_complete=True,
        completed_question=node,
        answer_text="\n".join([*prior_answer_texts, user_text]),
    )
