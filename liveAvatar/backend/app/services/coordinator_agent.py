"""Coordinator agent: follow-up recommendation plus an LLM-drafted invite.

`evaluate_followup` is pure, deterministic rules over the Appraiser's
Scorecard (no LLM, no I/O): a strong overall recommends a next-round
deep-dive, a borderline overall with weak categories recommends a
clarification call, anything else recommends nothing.

`draft_followup` makes one Gemini JSON call (same raw-httpx
OpenAI-compatible pattern as `appraiser_agent`/`summary_service`) to draft
the meeting package for a recommendation. Unlike the appraiser/host it
deliberately NEVER raises: any failure (missing API key, HTTP error,
unparsable JSON) logs a warning and falls back to a deterministic template
proposal, so a drafting hiccup can never suppress the recommendation.
"""

import logging
from dataclasses import dataclass
from typing import Literal

import httpx

from app.config import settings
from app.services.appraiser_agent import Scorecard
from app.services.interview_config import RubricCategory
from app.services.interview_state import InterviewState
from app.services.llm_json import parse_llm_json

logger = logging.getLogger(__name__)

# Strict structured output for the invite-drafting call (same rationale as
# host_agent._TURN_SCHEMA: prevents malformed-JSON completions).
_PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "agenda": {"type": "array", "items": {"type": "string"}},
        "duration_minutes": {"type": "integer"},
        "email_draft": {"type": "string"},
    },
    "required": ["title", "agenda", "duration_minutes", "email_draft"],
}

# Decision thresholds for evaluate_followup. These are logic constants, not
# deployment configuration, so they live here rather than in app.config.
ADVANCE_THRESHOLD = 3.5  # overall >= this -> recommend a next-round deep-dive
CLARIFY_FLOOR = 2.5  # overall in [CLARIFY_FLOOR, ADVANCE_THRESHOLD) may warrant clarification
WEAK_CATEGORY_MAX = 2  # a category scoring <= this counts as weak

FALLBACK_DURATION_MINUTES = 30


@dataclass
class FollowupRecommendation:
    kind: Literal["advance", "clarify"]
    reason: str  # template-built from the triggering rule, human-readable
    focus_categories: list[str]  # rubric category ids driving the agenda


@dataclass
class FollowupProposal:
    recommendation: FollowupRecommendation
    title: str
    agenda: list[str]
    duration_minutes: int
    email_draft: str


def _fmt(value: float) -> str:
    """Format a score without trailing zeros (3.5 -> '3.5', 4.0 -> '4')."""
    return f"{value:g}"


def evaluate_followup(
    scorecard: Scorecard, rubric: dict[str, RubricCategory]
) -> FollowupRecommendation | None:
    """Pure deterministic follow-up rules over a Scorecard (no LLM, no I/O)."""
    overall = scorecard.overall
    if overall is None:
        return None

    # Categories that have any data, in rubric order.
    by_id = {c.id: c for c in scorecard.categories}
    with_data = [by_id[cid] for cid in rubric if cid in by_id and by_id[cid].score is not None]

    if overall >= ADVANCE_THRESHOLD:
        # Focus the deep-dive on the two lowest-scoring categories with data;
        # the stable sort keeps rubric order among ties.
        focus = sorted(with_data, key=lambda c: c.score)[:2]  # type: ignore[arg-type, return-value]
        return FollowupRecommendation(
            kind="advance",
            reason=(
                f"Overall score {_fmt(overall)}/5 meets the advance threshold of "
                f"{_fmt(ADVANCE_THRESHOLD)}; a next-round deep-dive is warranted."
            ),
            focus_categories=[c.id for c in focus],
        )

    if overall >= CLARIFY_FLOOR:
        weak = [c for c in with_data if c.score <= WEAK_CATEGORY_MAX]  # type: ignore[operator]
        if weak:
            names = ", ".join(c.name for c in weak)
            return FollowupRecommendation(
                kind="clarify",
                reason=(
                    f"Overall score {_fmt(overall)}/5 is borderline, and these "
                    f"categories scored {WEAK_CATEGORY_MAX} or below: {names}. "
                    "A clarification call is recommended."
                ),
                focus_categories=[c.id for c in weak],
            )

    return None


def _focus_names(rec: FollowupRecommendation, rubric: dict[str, RubricCategory]) -> list[str]:
    return [rubric[cid].name if cid in rubric else cid for cid in rec.focus_categories]


def _render_user_content(
    state: InterviewState, rec: FollowupRecommendation, rubric: dict[str, RubricCategory]
) -> str:
    profile = state.vendor_profile
    contact = profile.contact_name + (f" ({profile.contact_role})" if profile.contact_role else "")
    lines = [
        "Vendor profile:",
        f"- Company: {profile.company_name}",
        f"- Contact: {contact}",
        "",
        f"Recommendation: {rec.kind}",
        f"Reason: {rec.reason}",
        "",
        "Focus categories:",
    ]
    for category_id in rec.focus_categories:
        name = rubric[category_id].name if category_id in rubric else category_id
        lines.append(f"- {name}")
        for score in state.scores:
            if category_id in score.category_scores and score.evidence:
                lines.append(f'  Evidence: "{score.evidence}"')
    if state.scout_findings:
        lines.extend(["", "Research findings:"])
        for finding in state.scout_findings:
            lines.append(f"- {finding.topic}: {finding.summary}")
    return "\n".join(lines)


def _fallback_proposal(
    state: InterviewState, rec: FollowupRecommendation, focus_names: list[str]
) -> FollowupProposal:
    """Deterministic template proposal used whenever Gemini drafting fails."""
    profile = state.vendor_profile
    topics = ", ".join(focus_names) if focus_names else "next steps"
    email_draft = (
        f"Dear {profile.contact_name},\n\n"
        f"Thank you for taking the time to speak with us. Following our "
        f"interview with {profile.company_name}, we would like to schedule a "
        f"follow-up meeting to discuss {topics}.\n\n"
        "Please let us know a time that works for you.\n\n"
        "Best regards,\n"
        "The Procurement Team"
    )
    return FollowupProposal(
        recommendation=rec,
        title=f"Follow-up meeting with {profile.company_name}",
        agenda=list(focus_names),
        duration_minutes=FALLBACK_DURATION_MINUTES,
        email_draft=email_draft,
    )


async def draft_followup(
    state: InterviewState, rec: FollowupRecommendation, rubric: dict[str, RubricCategory]
) -> FollowupProposal:
    """Draft the follow-up meeting package with a single Gemini JSON call.

    Never raises: on any failure it logs a warning and returns the template
    fallback proposal, so a drafting failure cannot suppress the
    recommendation."""
    focus_names = _focus_names(rec, rubric)
    try:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured; cannot draft the invite.")

        payload = {
            "model": settings.gemini_model,
            "messages": [
                {"role": "system", "content": settings.coordinator_invite_prompt},
                {"role": "user", "content": _render_user_content(state, rec, rubric)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "followup_proposal", "strict": True, "schema": _PROPOSAL_SCHEMA},
            },
            "reasoning_effort": "low",
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{settings.gemini_base_url}chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.gemini_api_key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

        parsed = parse_llm_json(data["choices"][0]["message"]["content"])
        return FollowupProposal(
            recommendation=rec,
            title=str(parsed["title"]),
            agenda=[str(item) for item in parsed["agenda"]],
            duration_minutes=int(parsed["duration_minutes"]),
            email_draft=str(parsed["email_draft"]),
        )
    except Exception:
        logger.warning(
            "Coordinator invite drafting failed for interview %s; using the template fallback.",
            state.interview_id,
            exc_info=True,
        )
        return _fallback_proposal(state, rec, focus_names)
