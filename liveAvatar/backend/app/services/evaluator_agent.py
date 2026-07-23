"""Evaluator agent: one holistic scoring pass over the finished interview.

`score_interview` runs once at finalize time (not per answer - a deliberate
design choice: the vendor never watches scores move mid-interview, and every
answer is judged in the context of the whole conversation). It renders the
full transcript, plus any independent Data Scout research findings, makes a
single Gemini call on the pro-tier model (`settings.gemini_pro_model` -
latency doesn't matter after the interview ends, so we buy the better
judgment), and asks for per-category "Signal Matrix" values (a closed label
per category, e.g. "Strategic"/"Exploring"/"Casual") with supporting
evidence quotes.

The LLM only proposes - all post-processing is done here in code: each
chosen label is resolved (case-insensitively) against the category's
`value_options` to its points, category ids outside the rubric are dropped,
categories the LLM omitted (or whose label didn't resolve) stay
`value=None`/`points=None`, and the overall is a weighted mean of points over
only the categories with data, with their rubric weights renormalized to sum
to 1.0.

Mirroring `summary_service`'s philosophy, it raises on every failure path;
the finalize router soft-fails so a scoring hiccup never loses the
transcript.
"""

import logging
from dataclasses import dataclass
from typing import Literal

from app.config import settings
from app.models import TranscriptTurn
from app.services import gemini_client
from app.services.interview_config import RubricCategory
from app.services.interview_state import ScoutFinding
from app.services.llm_json import parse_llm_json
from app.services.transcript_render import render_transcript

logger = logging.getLogger(__name__)

# overall >= this -> Scorecard.status = "APPROVED", else "REJECTED".
STATUS_THRESHOLD = 70

# Strict structured output for the holistic scoring call (same rationale as
# host_agent._TURN_SCHEMA: prevents malformed-JSON completions). `value`
# stays a generic string (not a per-category enum) since each category has
# its own label set; category ids and label validity are checked in code
# against the rubric.
_SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "categories": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                },
                "required": ["value", "evidence", "rationale"],
            },
        }
    },
    "required": ["categories"],
}


@dataclass
class CategoryScore:
    """One rubric category's result on the final scorecard. `value` is the
    label the LLM chose from that category's fixed `value_options`, and
    `points` is that label resolved to its rubric points; both are None when
    the category was never discussed (or the LLM's label failed to resolve),
    which excludes the category from the overall. `weight` is copied off the
    rubric so the overall's renormalization can happen downstream, and
    `evidence` holds the supporting quotes the LLM cited."""

    id: str
    name: str
    weight: float
    value: str | None  # chosen label; None when the category was never discussed
    points: float | None  # resolved points for `value`, for downstream arithmetic
    evidence: list[str]


@dataclass
class Scorecard:
    """The full evaluation result: one `CategoryScore` per rubric category (in
    rubric order), the 0-100 weighted-points `overall` (None until at least
    one category has data), and the `status` derived from it in code -
    "APPROVED" at or above `STATUS_THRESHOLD` else "REJECTED", and None while
    `overall` is still None."""

    categories: list[CategoryScore]  # one per rubric category, rubric order
    overall: float | None  # None until any category has data, 0-100
    status: Literal["APPROVED", "REJECTED"] | None  # None until overall is known


def _render_system_content(rubric: dict[str, RubricCategory]) -> str:
    """Build the scoring system message: the base evaluator prompt followed by
    one line per rubric category giving its id, name, description, and the
    exact allowed labels the LLM must choose among (the closed `value_options`
    label set) - so the model can only pick a valid categorical value."""
    lines = [settings.evaluator_system_prompt, "", "Rubric categories to score:"]
    for category in rubric.values():
        labels = ", ".join(option.label for option in category.value_options)
        lines.append(f"- {category.id} ({category.name}): {category.description} Allowed values: {labels}.")
    return "\n".join(lines)


def _render_findings(scout_findings: list[ScoutFinding]) -> str:
    """Render the Data Scout's independent research as a labelled block for the
    user message, so the Evaluator can weigh the vendor's claims against
    external findings. Each finding contributes its topic and summary, plus its
    source URL when one is present."""
    lines = ["Independent research findings (from internet, not from the vendor):"]
    for finding in scout_findings:
        lines.append(f"- Topic: {finding.topic}")
        lines.append(f"  Summary: {finding.summary}")
        if finding.source_url:
            lines.append(f"  Source: {finding.source_url}")
    return "\n".join(lines)


def _resolve_value(category: RubricCategory, raw_value: str) -> tuple[str, float] | None:
    """Case-insensitive match of the LLM's chosen label against the
    category's value_options. Returns None (soft-fail) if nothing matches -
    treated exactly like an omitted category, since there's no sensible
    "nearest" fallback for a categorical value."""
    needle = raw_value.strip().lower()
    for option in category.value_options:
        if option.label.strip().lower() == needle:
            return option.label, option.points
    return None


async def score_interview(
    turns: list[TranscriptTurn],
    rubric: dict[str, RubricCategory],
    scout_findings: list[ScoutFinding],
    vendor_context: str = "",
) -> Scorecard:
    """Score the whole interview with a single pro-model Gemini call and
    return the final Scorecard. `vendor_context` is the bullet summary of the
    intake material the vendor submitted up front (about-text/documents),
    included as clearly self-reported background. Raises on any HTTP/parse
    failure - the finalize router decides how to soft-fail."""
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured; cannot score the interview.")

    transcript_text = render_transcript(turns)
    if not transcript_text:
        raise ValueError("Transcript is empty; nothing to score.")
    candidate_turns = sum(1 for turn in turns if turn.role == "candidate")
    if candidate_turns < 2:
        raise ValueError("Transcript too short to score (fewer than 2 vendor answers).")

    user_content = f"Interview transcript:\n{transcript_text}"
    if vendor_context:
        user_content = (
            f"{user_content}\n\nVendor-provided background (self-reported by the vendor "
            f"before the interview, not independently verified):\n{vendor_context}"
        )
    if scout_findings:
        user_content = f"{user_content}\n\n{_render_findings(scout_findings)}"

    payload = {
        "model": settings.gemini_pro_model,
        "messages": [
            {"role": "system", "content": _render_system_content(rubric)},
            {"role": "user", "content": user_content},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "interview_score", "strict": True, "schema": _SCORE_SCHEMA},
        },
    }

    data = await gemini_client.chat_completion(
        payload, timeout=60.0, fallback_model=settings.gemini_pro_model_fallback
    )
    parsed = parse_llm_json(data["choices"][0]["message"]["content"])
    proposed = parsed.get("categories") or {}

    categories: list[CategoryScore] = []
    for category in rubric.values():
        entry = proposed.get(category.id)
        value = None
        points = None
        evidence: list[str] = []
        if isinstance(entry, dict) and entry.get("value") is not None:
            resolved = _resolve_value(category, str(entry["value"]))
            if resolved is not None:
                value, points = resolved
                evidence = [str(quote) for quote in (entry.get("evidence") or []) if str(quote).strip()]
        categories.append(
            CategoryScore(
                id=category.id,
                name=category.name,
                weight=category.weight,
                value=value,
                points=points,
                evidence=evidence,
            )
        )

    with_data = [c for c in categories if c.points is not None]
    overall = None
    status = None
    if with_data:
        # Renormalize the weights of the categories that have data to 1.0.
        total_weight = sum(c.weight for c in with_data)
        overall = round(sum(c.points * (c.weight / total_weight) for c in with_data), 1)
        status = "APPROVED" if overall >= STATUS_THRESHOLD else "REJECTED"

    return Scorecard(categories=categories, overall=overall, status=status)
