from app.services.coordinator_agent import (
    ADVANCE_THRESHOLD,
    CLARIFY_FLOOR,
    WEAK_CATEGORY_MAX,
    evaluate_followup,
)
from app.services.evaluator_agent import CategoryScore, Scorecard
from app.services.interview_config import RubricCategory


def make_rubric() -> dict[str, RubricCategory]:
    return {
        "experience": RubricCategory(id="experience", name="Experience", weight=0.3, description="Track record."),
        "capability": RubricCategory(id="capability", name="Capability", weight=0.3, description="Technical depth."),
        "delivery": RubricCategory(id="delivery", name="Delivery", weight=0.2, description="Team strength."),
        "credibility": RubricCategory(id="credibility", name="Credibility", weight=0.2, description="Trust signals."),
    }


def make_scorecard(
    scores: dict[str, float | None],
    overall: float | None,
    evidence: dict[str, list[str]] | None = None,
) -> Scorecard:
    """Hand-construct a Scorecard so the overall/category values are exact."""
    rubric = make_rubric()
    categories = [
        CategoryScore(
            id=c.id,
            name=c.name,
            weight=c.weight,
            score=scores.get(c.id),
            evidence=(evidence or {}).get(c.id, []),
        )
        for c in rubric.values()
    ]
    return Scorecard(categories=categories, overall=overall)


# --- evaluate_followup ---


def test_thresholds_module_constants():
    assert ADVANCE_THRESHOLD == 3.5
    assert CLARIFY_FLOOR == 2.5
    assert WEAK_CATEGORY_MAX == 2


def test_overall_none_returns_none():
    scorecard = make_scorecard({}, overall=None)

    assert evaluate_followup(scorecard, make_rubric()) is None


def test_overall_exactly_at_advance_threshold_advances():
    # 4*0.3 + 3*0.3 + 5*0.2 + 2*0.2 = 3.5 exactly.
    scorecard = make_scorecard(
        {"experience": 4.0, "capability": 3.0, "delivery": 5.0, "credibility": 2.0},
        overall=3.5,
    )

    rec = evaluate_followup(scorecard, make_rubric())

    assert rec is not None
    assert rec.kind == "advance"
    # The two lowest-scoring categories with data: credibility (2), capability (3).
    assert rec.focus_categories == ["credibility", "capability"]
    assert "3.5" in rec.reason
    assert "deep-dive" in rec.reason


def test_advance_focus_skips_categories_without_data_and_breaks_ties_in_rubric_order():
    # experience has NO data and must be skipped even though it is first in the
    # rubric; capability and delivery tie at 3 -> rubric order between them.
    scorecard = make_scorecard(
        {"experience": None, "capability": 3.0, "delivery": 3.0, "credibility": 5.0},
        overall=3.8,
    )

    rec = evaluate_followup(scorecard, make_rubric())

    assert rec is not None
    assert rec.kind == "advance"
    assert rec.focus_categories == ["capability", "delivery"]


def test_advance_with_single_category_with_data_focuses_only_it():
    scorecard = make_scorecard({"experience": 4.0}, overall=4.0)

    rec = evaluate_followup(scorecard, make_rubric())

    assert rec is not None
    assert rec.kind == "advance"
    assert rec.focus_categories == ["experience"]
    assert "4" in rec.reason


def test_mid_band_with_weak_categories_clarifies_listing_all_in_rubric_order():
    scorecard = make_scorecard(
        {"experience": 4.0, "capability": 2.0, "delivery": 2.0, "credibility": None},
        overall=3.0,
    )

    rec = evaluate_followup(scorecard, make_rubric())

    assert rec is not None
    assert rec.kind == "clarify"
    assert rec.focus_categories == ["capability", "delivery"]
    # The reason names the weak categories.
    assert "Capability" in rec.reason
    assert "Delivery" in rec.reason
    assert "Experience" not in rec.reason


def test_mid_band_without_weak_categories_returns_none():
    scorecard = make_scorecard({"experience": 3.0, "capability": 3.0}, overall=3.0)

    assert evaluate_followup(scorecard, make_rubric()) is None


def test_below_clarify_floor_returns_none_even_with_weak_categories():
    scorecard = make_scorecard({"experience": 2.0, "capability": 2.0}, overall=2.0)

    assert evaluate_followup(scorecard, make_rubric()) is None
