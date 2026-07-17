import json

import httpx
import respx

from app.services import coordinator_agent
from app.services.appraiser_agent import CategoryScore, Scorecard
from app.services.coordinator_agent import (
    ADVANCE_THRESHOLD,
    CLARIFY_FLOOR,
    WEAK_CATEGORY_MAX,
    FollowupProposal,
    FollowupRecommendation,
    draft_followup,
    evaluate_followup,
)
from app.services.interview_config import RubricCategory
from app.services.interview_state import AnswerScore, InterviewState, ScoutFinding, VendorProfile

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
CHAT_URL = f"{GEMINI_BASE_URL}chat/completions"


def make_rubric() -> dict[str, RubricCategory]:
    return {
        "experience": RubricCategory(id="experience", name="Experience", weight=0.3, description="Track record."),
        "capability": RubricCategory(id="capability", name="Capability", weight=0.3, description="Technical depth."),
        "delivery": RubricCategory(id="delivery", name="Delivery", weight=0.2, description="Team strength."),
        "credibility": RubricCategory(id="credibility", name="Credibility", weight=0.2, description="Trust signals."),
    }


def make_scorecard(scores: dict[str, float | None], overall: float | None) -> Scorecard:
    """Hand-construct a Scorecard so the overall/category values are exact."""
    rubric = make_rubric()
    categories = [
        CategoryScore(id=c.id, name=c.name, weight=c.weight, score=scores.get(c.id), evidence=[])
        for c in rubric.values()
    ]
    answered = len([s for s in scores.values() if s is not None])
    return Scorecard(categories=categories, overall=overall, answered_questions=answered)


def make_state() -> InterviewState:
    return InterviewState(
        interview_id="itest",
        gateway_token="tok",
        vendor_profile=VendorProfile(
            company_name="Acme Corp",
            website="https://acme.example",
            contact_name="Jane Doe",
            contact_role="CTO",
        ),
    )


def make_rec(
    kind: str = "advance",
    focus_categories: list[str] | None = None,
) -> FollowupRecommendation:
    return FollowupRecommendation(
        kind=kind,  # type: ignore[arg-type]
        reason="Overall score 4/5 meets the advance threshold.",
        focus_categories=focus_categories if focus_categories is not None else ["capability", "delivery"],
    )


def gemini_response(
    title: str = "Deep-dive with Acme Corp",
    agenda: list[str] | None = None,
    duration_minutes: int = 45,
    email_draft: str = "Dear Jane Doe, we would like to invite you to a follow-up meeting.",
) -> httpx.Response:
    body = {
        "title": title,
        "agenda": agenda if agenda is not None else ["Capability deep-dive", "Delivery walkthrough"],
        "duration_minutes": duration_minutes,
        "email_draft": email_draft,
    }
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(body)}}]})


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


# --- draft_followup ---


@respx.mock
async def test_draft_followup_happy_path(patch_settings):
    patched = patch_settings(
        gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL, gemini_model="gemini-3.5-flash"
    )
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state()
    state.scores.append(
        AnswerScore(
            question_id="q1",
            category_scores={"capability": 3},
            evidence="We shipped ML pipelines for three banks.",
            rationale="Concrete engagements.",
        )
    )
    state.scores.append(
        AnswerScore(
            question_id="q2",
            category_scores={"credibility": 4},
            evidence="Unrelated quote that must not appear.",
            rationale="",
        )
    )
    state.scout_findings.append(
        ScoutFinding(topic="funding", summary="Raised a Series B in 2025.", source_url=None)
    )
    rec = make_rec()

    proposal = await draft_followup(state, rec, make_rubric())

    assert isinstance(proposal, FollowupProposal)
    assert proposal.recommendation is rec
    assert proposal.title == "Deep-dive with Acme Corp"
    assert proposal.agenda == ["Capability deep-dive", "Delivery walkthrough"]
    assert proposal.duration_minutes == 45
    assert proposal.email_draft == "Dear Jane Doe, we would like to invite you to a follow-up meeting."

    # Request shape mirrors the other agents' raw-httpx Gemini calls.
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer gem-key"
    body = json.loads(request.content)
    assert body["model"] == "gemini-3.5-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0] == {"role": "system", "content": patched.coordinator_invite_prompt}
    user_content = body["messages"][-1]["content"]
    assert "Acme Corp" in user_content
    assert "Jane Doe" in user_content
    assert "CTO" in user_content
    assert rec.reason in user_content
    # Focus category names and only THEIR evidence quotes appear.
    assert "Capability" in user_content
    assert "Delivery" in user_content
    assert "We shipped ML pipelines for three banks." in user_content
    assert "Unrelated quote that must not appear." not in user_content
    # Scout finding summaries are included.
    assert "Raised a Series B in 2025." in user_content


@respx.mock
async def test_draft_followup_http_error_falls_back_to_template(patch_settings, caplog):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500))
    state = make_state()
    rec = make_rec()

    import logging

    with caplog.at_level(logging.WARNING, logger="app.services.coordinator_agent"):
        proposal = await draft_followup(state, rec, make_rubric())

    assert proposal.recommendation is rec
    assert proposal.recommendation.kind == "advance"
    assert proposal.recommendation.focus_categories == ["capability", "delivery"]
    assert "Acme Corp" in proposal.title
    # One agenda bullet per focus category name.
    assert len(proposal.agenda) == 2
    assert any("Capability" in item for item in proposal.agenda)
    assert any("Delivery" in item for item in proposal.agenda)
    assert proposal.duration_minutes == 30
    assert "Jane Doe" in proposal.email_draft
    assert "itest" in caplog.text


async def test_draft_followup_missing_api_key_falls_back_not_raises(patch_settings):
    patch_settings(gemini_api_key=None)
    state = make_state()
    rec = make_rec(kind="clarify", focus_categories=["delivery"])

    with respx.mock:
        proposal = await draft_followup(state, rec, make_rubric())
        assert len(respx.calls) == 0

    assert proposal.recommendation is rec
    assert "Acme Corp" in proposal.title
    assert proposal.agenda == ["Delivery"] or any("Delivery" in item for item in proposal.agenda)
    assert proposal.duration_minutes == 30


@respx.mock
async def test_draft_followup_malformed_json_falls_back(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "not json at all"}}]})
    )
    state = make_state()
    rec = make_rec()

    proposal = await draft_followup(state, rec, make_rubric())

    assert proposal.recommendation is rec
    assert proposal.duration_minutes == 30
    assert "Acme Corp" in proposal.title


# coordinator_agent must be listed in conftest._SETTINGS_IMPORTERS or
# patch_settings silently won't reach it; this guards against that regression.
def test_coordinator_agent_settings_are_patchable(patch_settings):
    patched = patch_settings(gemini_api_key="sentinel-key")
    assert coordinator_agent.settings is patched
