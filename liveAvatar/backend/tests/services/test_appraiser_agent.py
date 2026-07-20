import json

import httpx
import pytest
import respx

from app.models import TranscriptTurn
from app.services import appraiser_agent
from app.services.appraiser_agent import CategoryScore, Scorecard, score_interview

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
CHAT_URL = f"{GEMINI_BASE_URL}chat/completions"


def make_rubric():
    from app.services.interview_config import RubricCategory

    return {
        "experience": RubricCategory(id="experience", name="Experience", weight=0.3, description="Track record."),
        "capability": RubricCategory(id="capability", name="Capability", weight=0.3, description="Technical depth."),
        "delivery": RubricCategory(id="delivery", name="Delivery", weight=0.2, description="Team strength."),
        "credibility": RubricCategory(id="credibility", name="Credibility", weight=0.2, description="Trust signals."),
    }


def make_turns() -> list[TranscriptTurn]:
    return [
        TranscriptTurn(role="interviewer", text="Tell me about your company."),
        TranscriptTurn(role="candidate", text="We shipped ML pipelines for three banks."),
        TranscriptTurn(role="interviewer", text="How do you deliver?"),
        TranscriptTurn(role="candidate", text="Two-week sprints with a dedicated PM."),
    ]


def gemini_response(categories: dict | None = None) -> httpx.Response:
    body = {
        "categories": categories
        if categories is not None
        else {
            "experience": {
                "score": 4,
                "evidence": ["We shipped ML pipelines for three banks."],
                "rationale": "Concrete engagements.",
            },
            "delivery": {"score": 2, "evidence": [], "rationale": "Thin process detail."},
        }
    }
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(body)}}]})


# --- score_interview ---


@respx.mock
async def test_score_interview_happy_path(patch_settings):
    patch_settings(
        gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL, gemini_pro_model="gemini-pro-latest"
    )
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())

    scorecard = await score_interview(make_turns(), make_rubric())

    assert isinstance(scorecard, Scorecard)
    assert [c.id for c in scorecard.categories] == ["experience", "capability", "delivery", "credibility"]
    by_id = {c.id: c for c in scorecard.categories}
    assert isinstance(by_id["experience"], CategoryScore)
    assert by_id["experience"].score == 4.0
    assert by_id["experience"].evidence == ["We shipped ML pipelines for three banks."]
    assert by_id["delivery"].score == 2.0
    assert by_id["delivery"].evidence == []
    # Omitted categories stay unscored.
    assert by_id["capability"].score is None
    assert by_id["credibility"].score is None
    # overall = 4 * (0.3 / 0.5) + 2 * (0.2 / 0.5) = 2.4 + 0.8 = 3.2
    assert scorecard.overall == 3.2

    # One pro-model call over the whole transcript.
    assert route.call_count == 1
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer gem-key"
    body = json.loads(request.content)
    assert body["model"] == "gemini-pro-latest"
    assert body["response_format"]["type"] == "json_schema"
    schema_spec = body["response_format"]["json_schema"]
    assert schema_spec["name"] == "interview_score"
    assert schema_spec["strict"] is True
    assert schema_spec["schema"]["required"] == ["categories"]
    # No reasoning_effort cap: at finalize we want the model to think.
    assert "reasoning_effort" not in body
    system_content = body["messages"][0]["content"]
    # ALL rubric categories are offered (the model decides which were discussed).
    for category_id in make_rubric():
        assert category_id in system_content
    user_content = body["messages"][-1]["content"]
    assert "Interviewer: Tell me about your company." in user_content
    assert "Candidate: Two-week sprints with a dedicated PM." in user_content


@respx.mock
async def test_scores_clamped_to_int_0_to_5(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response(
            {
                "experience": {"score": 7, "evidence": [], "rationale": ""},
                "capability": {"score": -1, "evidence": [], "rationale": ""},
            }
        )
    )

    scorecard = await score_interview(make_turns(), make_rubric())

    by_id = {c.id: c for c in scorecard.categories}
    assert by_id["experience"].score == 5.0
    assert by_id["capability"].score == 0.0


@respx.mock
async def test_unknown_categories_dropped(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response(
            {
                "experience": {"score": 4, "evidence": [], "rationale": ""},
                "made_up": {"score": 5, "evidence": [], "rationale": ""},
            }
        )
    )

    scorecard = await score_interview(make_turns(), make_rubric())

    assert {c.id for c in scorecard.categories} == {"experience", "capability", "delivery", "credibility"}
    assert scorecard.overall == 4.0  # only experience has data


@respx.mock
async def test_blank_evidence_quotes_dropped(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response(
            {"experience": {"score": 3, "evidence": ["  ", "Real quote."], "rationale": ""}}
        )
    )

    scorecard = await score_interview(make_turns(), make_rubric())

    experience = next(c for c in scorecard.categories if c.id == "experience")
    assert experience.evidence == ["Real quote."]


@respx.mock
async def test_full_coverage_plain_weighted_sum(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response(
            {
                "experience": {"score": 4, "evidence": [], "rationale": ""},
                "capability": {"score": 3, "evidence": [], "rationale": ""},
                "delivery": {"score": 5, "evidence": [], "rationale": ""},
                "credibility": {"score": 2, "evidence": [], "rationale": ""},
            }
        )
    )

    scorecard = await score_interview(make_turns(), make_rubric())

    # 4*0.3 + 3*0.3 + 5*0.2 + 2*0.2 = 1.2 + 0.9 + 1.0 + 0.4 = 3.5
    assert scorecard.overall == 3.5


@respx.mock
async def test_no_categories_scored_overall_none(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=gemini_response({}))

    scorecard = await score_interview(make_turns(), make_rubric())

    assert all(c.score is None for c in scorecard.categories)
    assert scorecard.overall is None


@respx.mock
async def test_http_error_raises(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        await score_interview(make_turns(), make_rubric())


@respx.mock
async def test_malformed_json_raises(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "not json at all"}}]})
    )

    with pytest.raises(ValueError, match="No JSON object"):
        await score_interview(make_turns(), make_rubric())


@respx.mock
async def test_fenced_json_content_is_parsed(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    content = '```json\n{"categories": {"experience": {"score": 5, "evidence": ["q"], "rationale": "r"}}}\n```'
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    )

    scorecard = await score_interview(make_turns(), make_rubric())

    experience = next(c for c in scorecard.categories if c.id == "experience")
    assert experience.score == 5.0
    assert experience.evidence == ["q"]


async def test_missing_gemini_key_raises(patch_settings):
    patch_settings(gemini_api_key=None)
    with respx.mock:
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            await score_interview(make_turns(), make_rubric())
        assert len(respx.calls) == 0


async def test_empty_transcript_raises(patch_settings):
    patch_settings(gemini_api_key="gem-key")
    with respx.mock:
        with pytest.raises(ValueError, match="empty"):
            await score_interview([], make_rubric())
        assert len(respx.calls) == 0


# appraiser_agent must be listed in conftest._SETTINGS_IMPORTERS or
# patch_settings silently won't reach it; this guards against that regression.
def test_appraiser_agent_settings_are_patchable(patch_settings):
    patched = patch_settings(gemini_api_key="sentinel-key")
    assert appraiser_agent.settings is patched
