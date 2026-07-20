"""Coordinator agent: scorecard vs threshold -> recommendation for the human
evaluator.

`evaluate_followup` is the whole agent: pure, deterministic rules over the
Evaluator's Scorecard (no LLM, no I/O). A strong overall recommends a
next-round deep-dive, a borderline overall with weak categories recommends a
clarification call, anything else recommends nothing. The recommendation is
handed to a human procurement lead to act on - the Coordinator does not draft
anything itself.
"""

from dataclasses import dataclass
from typing import Literal

from app.services.evaluator_agent import Scorecard
from app.services.interview_config import RubricCategory

# Decision thresholds for evaluate_followup. These are logic constants, not
# deployment configuration, so they live here rather than in app.config.
ADVANCE_THRESHOLD = 3.5  # overall >= this -> recommend a next-round deep-dive
CLARIFY_FLOOR = 2.5  # overall in [CLARIFY_FLOOR, ADVANCE_THRESHOLD) may warrant clarification
WEAK_CATEGORY_MAX = 2  # a category scoring <= this counts as weak


@dataclass
class FollowupRecommendation:
    kind: Literal["advance", "clarify"]
    reason: str  # template-built from the triggering rule, human-readable
    focus_categories: list[str]  # rubric category ids driving the agenda


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
