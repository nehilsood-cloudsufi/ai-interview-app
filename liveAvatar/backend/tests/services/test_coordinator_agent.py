from app.services.coordinator_agent import (
    ADVANCE_THRESHOLD,
    CLARIFY_FLOOR,
    WEAK_CATEGORY_MAX_POINTS,
    evaluate_followup,
)
from app.services.evaluator_agent import CategoryScore, Scorecard
from app.services.interview_config import RubricCategory, ValueOption

_GENERIC_OPTIONS = [ValueOption(label="High", points=100), ValueOption(label="Low", points=0)]


def make_rubric() -> dict[str, RubricCategory]:
    return {
        "experience": RubricCategory(
            id="experience",
            name="Experience",
            weight=0.3,
            description="Track record.",
            value_options=_GENERIC_OPTIONS,
        ),
        "capability": RubricCategory(
            id="capability",
            name="Capability",
            weight=0.3,
            description="Technical depth.",
            value_options=_GENERIC_OPTIONS,
        ),
        "delivery": RubricCategory(
            id="delivery", name="Delivery", weight=0.2, description="Team strength.", value_options=_GENERIC_OPTIONS
        ),
        "credibility": RubricCategory(
            id="credibility",
            name="Credibility",
            weight=0.2,
            description="Trust signals.",
            value_options=_GENERIC_OPTIONS,
        ),
    }


def make_scorecard(
    points: dict[str, float | None],
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
            value="High" if points.get(c.id) is not None else None,
            points=points.get(c.id),
            evidence=(evidence or {}).get(c.id, []),
        )
        for c in rubric.values()
    ]
    return Scorecard(categories=categories, overall=overall, status=None)


# --- evaluate_followup ---
#
# All numeric fixtures below are the old 0-5 scale rescaled x20 to the new
# 0-100 points scale (4.0->80, 3.0->60, 5.0->100, 2.0->40) - the underlying
# ordering/tie-break/rubric-order logic is unchanged, only the numbers moved.


def test_thresholds_module_constants():
    assert ADVANCE_THRESHOLD == 70
    assert CLARIFY_FLOOR == 50
    assert WEAK_CATEGORY_MAX_POINTS == 40


def test_overall_none_returns_none():
    scorecard = make_scorecard({}, overall=None)

    assert evaluate_followup(scorecard, make_rubric()) is None


def test_overall_exactly_at_advance_threshold_advances():
    # 80*0.3 + 60*0.3 + 100*0.2 + 40*0.2 = 24 + 18 + 20 + 8 = 70 exactly.
    scorecard = make_scorecard(
        {"experience": 80.0, "capability": 60.0, "delivery": 100.0, "credibility": 40.0},
        overall=70.0,
    )

    rec = evaluate_followup(scorecard, make_rubric())

    assert rec is not None
    assert rec.kind == "advance"
    # The two lowest-scoring categories with data: credibility (40), capability (60).
    assert rec.focus_categories == ["credibility", "capability"]
    assert "70" in rec.reason
    assert "/100" in rec.reason
    assert "deep-dive" in rec.reason


def test_advance_focus_skips_categories_without_data_and_breaks_ties_in_rubric_order():
    # experience has NO data and must be skipped even though it is first in the
    # rubric; capability and delivery tie at 60 -> rubric order between them.
    scorecard = make_scorecard(
        {"experience": None, "capability": 60.0, "delivery": 60.0, "credibility": 100.0},
        overall=76.0,
    )

    rec = evaluate_followup(scorecard, make_rubric())

    assert rec is not None
    assert rec.kind == "advance"
    assert rec.focus_categories == ["capability", "delivery"]


def test_advance_with_single_category_with_data_focuses_only_it():
    scorecard = make_scorecard({"experience": 80.0}, overall=80.0)

    rec = evaluate_followup(scorecard, make_rubric())

    assert rec is not None
    assert rec.kind == "advance"
    assert rec.focus_categories == ["experience"]
    assert "80" in rec.reason


def test_mid_band_with_weak_categories_clarifies_listing_all_in_rubric_order():
    scorecard = make_scorecard(
        {"experience": 80.0, "capability": 40.0, "delivery": 40.0, "credibility": None},
        overall=60.0,
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
    scorecard = make_scorecard({"experience": 60.0, "capability": 60.0}, overall=60.0)

    assert evaluate_followup(scorecard, make_rubric()) is None


def test_below_clarify_floor_returns_none_even_with_weak_categories():
    scorecard = make_scorecard({"experience": 40.0, "capability": 40.0}, overall=40.0)

    assert evaluate_followup(scorecard, make_rubric()) is None
