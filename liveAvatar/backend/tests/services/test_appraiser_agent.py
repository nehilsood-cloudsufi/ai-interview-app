import json
import logging

import httpx
import pytest
import respx

from app.services import appraiser_agent
from app.services.appraiser_agent import (
    CategoryScore,
    Scorecard,
    compute_scorecard,
    score_and_store,
    score_answer,
)
from app.services.interview_config import Branch, QuestionNode, RubricCategory
from app.services.interview_state import AnswerScore, InterviewState, VendorProfile

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
CHAT_URL = f"{GEMINI_BASE_URL}chat/completions"


def make_rubric() -> dict[str, RubricCategory]:
    return {
        "experience": RubricCategory(id="experience", name="Experience", weight=0.3, description="Track record."),
        "capability": RubricCategory(id="capability", name="Capability", weight=0.3, description="Technical depth."),
        "delivery": RubricCategory(id="delivery", name="Delivery", weight=0.2, description="Team strength."),
        "credibility": RubricCategory(id="credibility", name="Credibility", weight=0.2, description="Trust signals."),
    }


def make_question(rubric_categories: list[str] | None = None) -> QuestionNode:
    return QuestionNode(
        id="company_overview",
        topic="company_overview",
        ask="Ask for a brief overview of the company.",
        rubric_categories=rubric_categories if rubric_categories is not None else ["experience", "capability"],
        branches=[Branch(signal="default", next="closing")],
        max_followups=1,
    )


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


def gemini_response(
    category_scores: dict | None = None,
    evidence: str | None = "We shipped ML pipelines for three banks.",
    rationale: str | None = "Concrete engagements with named outcomes.",
) -> httpx.Response:
    body: dict = {"category_scores": category_scores if category_scores is not None else {"experience": 4, "capability": 3}}
    if evidence is not None:
        body["evidence"] = evidence
    if rationale is not None:
        body["rationale"] = rationale
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(body)}}]})


# --- score_answer ---


@respx.mock
async def test_score_answer_parses_fenced_json(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    content = '```json\n{"category_scores": {"experience": 5}, "evidence": "quote", "rationale": "why"}\n```'
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    )
    state = make_state()

    result = await score_answer(state, make_question(), "Answer.", make_rubric())

    assert result.category_scores == {"experience": 5}
    assert result.evidence == "quote"


@respx.mock
async def test_score_answer_happy_path(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL, gemini_model="gemini-3.5-flash")
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state()
    question = make_question()

    result = await score_answer(state, question, "We build ML pipelines for banks.", make_rubric())

    assert isinstance(result, AnswerScore)
    assert result.question_id == "company_overview"
    assert result.category_scores == {"experience": 4, "capability": 3}
    assert result.evidence == "We shipped ML pipelines for three banks."
    assert result.rationale == "Concrete engagements with named outcomes."
    assert state.scores == [result]

    # Request shape mirrors summary_service's raw-httpx Gemini call.
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer gem-key"
    body = json.loads(request.content)
    assert body["model"] == "gemini-3.5-flash"
    assert body["response_format"]["type"] == "json_schema"
    schema_spec = body["response_format"]["json_schema"]
    assert schema_spec["name"] == "answer_score"
    assert schema_spec["strict"] is True
    assert set(schema_spec["schema"]["required"]) == {"category_scores", "evidence", "rationale"}
    assert body["reasoning_effort"] == "low"
    assert body["messages"][0]["role"] == "system"
    system_content = body["messages"][0]["content"]
    # Only the question's rubric categories are offered for scoring.
    assert "experience" in system_content
    assert "capability" in system_content
    assert "delivery" not in system_content
    assert "credibility" not in system_content
    assert body["messages"][-1]["role"] == "user"
    user_content = body["messages"][-1]["content"]
    assert "Ask for a brief overview of the company." in user_content
    assert "We build ML pipelines for banks." in user_content


@respx.mock
async def test_scores_clamped_to_int_0_to_5(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=gemini_response(category_scores={"experience": 7, "capability": -1}))
    state = make_state()

    result = await score_answer(state, make_question(), "An answer.", make_rubric())

    assert result.category_scores == {"experience": 5, "capability": 0}


@respx.mock
async def test_float_score_rounds_to_nearest_int(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=gemini_response(category_scores={"experience": 3.7, "capability": 2.2}))
    state = make_state()

    result = await score_answer(state, make_question(), "An answer.", make_rubric())

    # int(round()) semantics.
    assert result.category_scores == {"experience": 4, "capability": 2}


@respx.mock
async def test_unknown_categories_dropped(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response(category_scores={"experience": 4, "delivery": 5, "made_up": 3})
    )
    state = make_state()

    result = await score_answer(state, make_question(), "An answer.", make_rubric())

    # "delivery" is a real rubric category but not one of this question's.
    assert result.category_scores == {"experience": 4}


@respx.mock
async def test_missing_evidence_and_rationale_default_to_empty(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=gemini_response(evidence=None, rationale=None))
    state = make_state()

    result = await score_answer(state, make_question(), "An answer.", make_rubric())

    assert result.evidence == ""
    assert result.rationale == ""


@respx.mock
async def test_http_error_raises_and_leaves_scores_unchanged(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500))
    state = make_state()

    with pytest.raises(httpx.HTTPStatusError):
        await score_answer(state, make_question(), "An answer.", make_rubric())

    assert state.scores == []


@respx.mock
async def test_malformed_json_raises(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "not json at all"}}]})
    )
    state = make_state()

    with pytest.raises(ValueError, match="No JSON object"):
        await score_answer(state, make_question(), "An answer.", make_rubric())

    assert state.scores == []


async def test_missing_gemini_key_raises(patch_settings):
    patch_settings(gemini_api_key=None)
    state = make_state()
    with respx.mock:
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            await score_answer(state, make_question(), "An answer.", make_rubric())
        assert len(respx.calls) == 0


# --- score_and_store ---


@respx.mock
async def test_score_and_store_swallows_and_logs_failure(patch_settings, caplog):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500))
    state = make_state()

    with caplog.at_level(logging.WARNING, logger="app.services.appraiser_agent"):
        await score_and_store(state, make_question(), "An answer.", make_rubric())

    assert state.scores == []
    assert "itest" in caplog.text
    assert "company_overview" in caplog.text


@respx.mock
async def test_score_and_store_appends_on_success(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state()

    await score_and_store(state, make_question(), "An answer.", make_rubric())

    assert len(state.scores) == 1
    assert state.scores[0].category_scores == {"experience": 4, "capability": 3}


# --- compute_scorecard ---


def answer(question_id: str, category_scores: dict[str, int], evidence: str = "") -> AnswerScore:
    return AnswerScore(question_id=question_id, category_scores=category_scores, evidence=evidence, rationale="")


def test_scorecard_empty_scores():
    scorecard = compute_scorecard([], make_rubric())

    assert isinstance(scorecard, Scorecard)
    assert [c.id for c in scorecard.categories] == ["experience", "capability", "delivery", "credibility"]
    assert all(c.score is None for c in scorecard.categories)
    assert all(c.evidence == [] for c in scorecard.categories)
    assert scorecard.overall is None
    assert scorecard.answered_questions == 0


def test_scorecard_single_answer_single_category():
    scores = [answer("q1", {"experience": 4}, evidence="A quote.")]

    scorecard = compute_scorecard(scores, make_rubric())

    by_id = {c.id: c for c in scorecard.categories}
    assert isinstance(by_id["experience"], CategoryScore)
    assert by_id["experience"].name == "Experience"
    assert by_id["experience"].weight == 0.3
    assert by_id["experience"].score == 4.0
    assert by_id["experience"].evidence == ["A quote."]
    assert by_id["capability"].score is None
    assert by_id["delivery"].score is None
    assert by_id["credibility"].score is None
    # Only one category has data; its weight renormalizes to 1.0.
    assert scorecard.overall == 4.0
    assert scorecard.answered_questions == 1


def test_scorecard_mean_over_multiple_answers_same_category():
    scores = [
        answer("q1", {"experience": 4}, evidence="First quote."),
        answer("q2", {"experience": 3}, evidence="Second quote."),
        answer("q3", {"experience": 4}),
    ]

    scorecard = compute_scorecard(scores, make_rubric())

    experience = next(c for c in scorecard.categories if c.id == "experience")
    # mean(4, 3, 4) = 3.666... -> 3.67; empty evidence strings are dropped.
    assert experience.score == 3.67
    assert experience.evidence == ["First quote.", "Second quote."]
    assert scorecard.overall == 3.67
    assert scorecard.answered_questions == 3


def test_scorecard_partial_coverage_renormalizes_weights():
    # Weights {experience: 0.3, capability: 0.3, delivery: 0.2, credibility: 0.2};
    # only experience (mean 4.0) and delivery (mean 2.0) have data.
    scores = [
        answer("q1", {"experience": 4}),
        answer("q2", {"delivery": 2}),
    ]

    scorecard = compute_scorecard(scores, make_rubric())

    # overall = 4.0 * (0.3 / 0.5) + 2.0 * (0.2 / 0.5) = 2.4 + 0.8 = 3.2
    assert scorecard.overall == 3.2
    assert scorecard.answered_questions == 2


def test_scorecard_full_coverage_plain_weighted_sum():
    scores = [
        answer("q1", {"experience": 4, "capability": 3}),
        answer("q2", {"delivery": 5, "credibility": 2}),
    ]

    scorecard = compute_scorecard(scores, make_rubric())

    # 4*0.3 + 3*0.3 + 5*0.2 + 2*0.2 = 1.2 + 0.9 + 1.0 + 0.4 = 3.5
    assert scorecard.overall == 3.5
    assert scorecard.answered_questions == 2


def test_scorecard_answered_questions_counts_distinct_question_ids():
    scores = [
        answer("q1", {"experience": 4}),
        answer("q1", {"capability": 3}),
        answer("q2", {"experience": 2}),
    ]

    scorecard = compute_scorecard(scores, make_rubric())

    assert scorecard.answered_questions == 2


# appraiser_agent must be listed in conftest._SETTINGS_IMPORTERS or
# patch_settings silently won't reach it; this guards against that regression.
def test_appraiser_agent_settings_are_patchable(patch_settings):
    patched = patch_settings(gemini_api_key="sentinel-key")
    assert appraiser_agent.settings is patched
