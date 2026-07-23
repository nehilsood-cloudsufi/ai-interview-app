import json

import httpx
import pytest
import respx

from app.models import TranscriptTurn
from app.services import evaluator_agent
from app.services.evaluator_agent import CategoryScore, Scorecard, score_interview
from app.services.interview_config import RubricCategory, ValueOption
from app.services.interview_state import ScoutFinding

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
CHAT_URL = f"{GEMINI_BASE_URL}chat/completions"


def make_rubric():
    # Deliberately generic, fixture-only category ids/labels (not the real
    # production Signal Matrix categories) - two independent categories,
    # weight=0.5 apiece, each with its own small closed label set.
    return {
        "quality": RubricCategory(
            id="quality",
            name="Quality",
            weight=0.5,
            description="Answer quality.",
            value_options=[ValueOption(label="Good", points=100), ValueOption(label="Bad", points=0)],
        ),
        "speed": RubricCategory(
            id="speed",
            name="Speed",
            weight=0.5,
            description="Delivery speed.",
            value_options=[ValueOption(label="Fast", points=100), ValueOption(label="Slow", points=50)],
        ),
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
            "quality": {
                "value": "Good",
                "evidence": ["We shipped ML pipelines for three banks."],
                "rationale": "Concrete engagements.",
            },
            "speed": {"value": "Slow", "evidence": [], "rationale": "Thin process detail."},
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

    scorecard = await score_interview(make_turns(), make_rubric(), [])

    assert isinstance(scorecard, Scorecard)
    assert [c.id for c in scorecard.categories] == ["quality", "speed"]
    by_id = {c.id: c for c in scorecard.categories}
    assert isinstance(by_id["quality"], CategoryScore)
    assert by_id["quality"].value == "Good"
    assert by_id["quality"].points == 100.0
    assert by_id["quality"].evidence == ["We shipped ML pipelines for three banks."]
    assert by_id["speed"].value == "Slow"
    assert by_id["speed"].points == 50.0
    assert by_id["speed"].evidence == []
    # overall = 100 * 0.5 + 50 * 0.5 = 50 + 25 = 75.0 (both categories have
    # data, so no weight renormalization is needed).
    assert scorecard.overall == 75.0
    assert scorecard.status == "APPROVED"  # 75 >= STATUS_THRESHOLD (70)

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
async def test_invalid_value_is_dropped_softly(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response(
            {
                "quality": {"value": "Amazing", "evidence": ["irrelevant"], "rationale": ""},
                "speed": {"value": "Fast", "evidence": [], "rationale": ""},
            }
        )
    )

    scorecard = await score_interview(make_turns(), make_rubric(), [])

    by_id = {c.id: c for c in scorecard.categories}
    # "Amazing" doesn't match any of quality's value_options (Good/Bad) - this
    # is a soft-fail, not an exception: treated exactly like an omitted
    # category.
    assert by_id["quality"].value is None
    assert by_id["quality"].points is None
    assert by_id["quality"].evidence == []
    assert by_id["speed"].value == "Fast"
    assert by_id["speed"].points == 100.0
    # Only speed has data -> its weight renormalizes to 1.0.
    assert scorecard.overall == 100.0
    assert scorecard.status == "APPROVED"


@respx.mock
async def test_unknown_categories_dropped(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response(
            {
                "quality": {"value": "Good", "evidence": [], "rationale": ""},
                "made_up": {"value": "Whatever", "evidence": [], "rationale": ""},
            }
        )
    )

    scorecard = await score_interview(make_turns(), make_rubric(), [])

    assert {c.id for c in scorecard.categories} == {"quality", "speed"}
    # only quality has data -> its weight renormalizes to 1.0
    assert scorecard.overall == 100.0


@respx.mock
async def test_blank_evidence_quotes_dropped(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response(
            {"quality": {"value": "Good", "evidence": ["  ", "Real quote."], "rationale": ""}}
        )
    )

    scorecard = await score_interview(make_turns(), make_rubric(), [])

    quality = next(c for c in scorecard.categories if c.id == "quality")
    assert quality.evidence == ["Real quote."]


@respx.mock
async def test_full_coverage_plain_weighted_sum(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response(
            {
                "quality": {"value": "Bad", "evidence": [], "rationale": ""},
                "speed": {"value": "Fast", "evidence": [], "rationale": ""},
            }
        )
    )

    scorecard = await score_interview(make_turns(), make_rubric(), [])

    # 0 * 0.5 + 100 * 0.5 = 50.0 - both categories have data so the rubric's
    # own weights (which already sum to 1.0) are used unchanged.
    assert scorecard.overall == 50.0


@respx.mock
async def test_no_categories_scored_overall_none(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=gemini_response({}))

    scorecard = await score_interview(make_turns(), make_rubric(), [])

    assert all(c.value is None and c.points is None for c in scorecard.categories)
    assert scorecard.overall is None
    assert scorecard.status is None


@respx.mock
async def test_http_error_raises(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        await score_interview(make_turns(), make_rubric(), [])


@respx.mock
async def test_malformed_json_raises(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "not json at all"}}]})
    )

    with pytest.raises(ValueError, match="No JSON object"):
        await score_interview(make_turns(), make_rubric(), [])


@respx.mock
async def test_fenced_json_content_is_parsed(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    content = '```json\n{"categories": {"quality": {"value": "Good", "evidence": ["q"], "rationale": "r"}}}\n```'
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    )

    scorecard = await score_interview(make_turns(), make_rubric(), [])

    quality = next(c for c in scorecard.categories if c.id == "quality")
    assert quality.value == "Good"
    assert quality.points == 100.0
    assert quality.evidence == ["q"]


async def test_missing_gemini_key_raises(patch_settings):
    patch_settings(gemini_api_key=None)
    with respx.mock:
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            await score_interview(make_turns(), make_rubric(), [])
        assert len(respx.calls) == 0


async def test_empty_transcript_raises(patch_settings):
    patch_settings(gemini_api_key="gem-key")
    with respx.mock:
        with pytest.raises(ValueError, match="empty"):
            await score_interview([], make_rubric(), [])
        assert len(respx.calls) == 0


# --- status thresholding ---


def make_threshold_rubric():
    # A single category whose weight renormalizes to 1.0 regardless of the
    # nominal weight value, so `overall` is exactly the category's points -
    # letting these boundary tests hit STATUS_THRESHOLD (70) precisely.
    return {
        "quality": RubricCategory(
            id="quality",
            name="Quality",
            weight=1.0,
            description="Answer quality.",
            value_options=[
                ValueOption(label="Right-At-Threshold", points=70),
                ValueOption(label="Just-Below-Threshold", points=69),
            ],
        ),
    }


@respx.mock
async def test_status_approved_at_or_above_threshold(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response({"quality": {"value": "Right-At-Threshold", "evidence": [], "rationale": ""}})
    )

    scorecard = await score_interview(make_turns(), make_threshold_rubric(), [])

    assert scorecard.overall == 70.0
    assert scorecard.status == "APPROVED"


@respx.mock
async def test_status_rejected_below_threshold(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response({"quality": {"value": "Just-Below-Threshold", "evidence": [], "rationale": ""}})
    )

    scorecard = await score_interview(make_turns(), make_threshold_rubric(), [])

    assert scorecard.overall == 69.0
    assert scorecard.status == "REJECTED"


# --- scout_findings ---


@respx.mock
async def test_scout_findings_rendered_when_present(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    findings = [
        ScoutFinding(topic="funding", summary="Raised a Series B in 2025.", source_url="https://news.example/a"),
        ScoutFinding(topic="reviews", summary="Mixed reviews on delivery speed.", source_url=None),
    ]

    await score_interview(make_turns(), make_rubric(), findings)

    user_content = json.loads(route.calls[0].request.content)["messages"][-1]["content"]
    assert "Independent research findings (from internet, not from the vendor):" in user_content
    assert "funding" in user_content
    assert "Raised a Series B in 2025." in user_content
    assert "https://news.example/a" in user_content
    assert "reviews" in user_content
    assert "Mixed reviews on delivery speed." in user_content


@respx.mock
async def test_scout_findings_section_omitted_when_empty(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())

    await score_interview(make_turns(), make_rubric(), [])

    user_content = json.loads(route.calls[0].request.content)["messages"][-1]["content"]
    assert "Independent research findings" not in user_content
    assert user_content == "Interview transcript:\n" + (
        "Interviewer: Tell me about your company.\n"
        "Candidate: We shipped ML pipelines for three banks.\n"
        "Interviewer: How do you deliver?\n"
        "Candidate: Two-week sprints with a dedicated PM."
    )


@respx.mock
async def test_scout_findings_do_not_change_scoring_behavior(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=gemini_response())
    findings = [ScoutFinding(topic="funding", summary="Raised a Series B.", source_url=None)]

    scorecard = await score_interview(make_turns(), make_rubric(), findings)

    by_id = {c.id: c for c in scorecard.categories}
    assert by_id["quality"].points == 100.0
    assert scorecard.overall == 75.0


# evaluator_agent must be listed in conftest._SETTINGS_IMPORTERS or
# patch_settings silently won't reach it; this guards against that regression.
def test_evaluator_agent_settings_are_patchable(patch_settings):
    patched = patch_settings(gemini_api_key="sentinel-key")
    assert evaluator_agent.settings is patched


@respx.mock
async def test_vendor_context_included_as_self_reported_background(patch_settings):
    patch_settings(gemini_api_key="gem-key")
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())

    await score_interview(
        make_turns(), make_rubric(), [], vendor_context="- Builds reusable rockets"
    )

    user_content = json.loads(route.calls[0].request.content)["messages"][1]["content"]
    assert "- Builds reusable rockets" in user_content
    # Framed as unverified self-reporting so the Evaluator weighs it properly.
    assert "self-reported" in user_content


@respx.mock
async def test_no_vendor_context_block_when_empty(patch_settings):
    patch_settings(gemini_api_key="gem-key")
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())

    await score_interview(make_turns(), make_rubric(), [])

    user_content = json.loads(route.calls[0].request.content)["messages"][1]["content"]
    assert "self-reported" not in user_content
