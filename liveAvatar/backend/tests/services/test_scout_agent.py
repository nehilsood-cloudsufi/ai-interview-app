import json
import logging

import httpx
import pytest
import respx

from app.services import interview_state, scout_agent
from app.services.interview_state import ScoutFinding, VendorProfile
from app.services.scout_agent import GeminiSearchProvider, run

NATIVE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/"


def make_state(company_name="Acme Corp"):
    profile = VendorProfile(company_name=company_name)
    return interview_state.create(profile, "ai_ml")


def model_url(model: str) -> str:
    return f"{NATIVE_BASE_URL}models/{model}:generateContent"


def gemini_response(findings: list | None, grounding_chunks: list | None = None) -> httpx.Response:
    body = {
        "findings": findings
        if findings is not None
        else [
            {"topic": "Overview", "summary": "A mid-size vendor.", "source_url": "https://acme.example/about"},
            {"topic": "Products", "summary": "Sells widgets.", "source_url": None},
        ]
    }
    candidate = {"content": {"parts": [{"text": json.dumps(body)}]}}
    if grounding_chunks is not None:
        candidate["groundingMetadata"] = {"groundingChunks": grounding_chunks}
    return httpx.Response(200, json={"candidates": [candidate]})


# --- run() ---


@respx.mock
async def test_happy_path_findings_parsed_and_backfilled(patch_settings):
    patch_settings(
        gemini_api_key="gem-key",
        gemini_native_base_url=NATIVE_BASE_URL,
        gemini_model="gemini-flash-latest",
        scout_enabled=True,
    )
    grounding_chunks = [
        {"web": {"uri": "https://news.example/acme-1"}},
        {"web": {"uri": "https://news.example/acme-2"}},
    ]
    route = respx.post(model_url("gemini-flash-latest")).mock(
        return_value=gemini_response(None, grounding_chunks=grounding_chunks)
    )

    state = make_state()
    findings = await run(state)

    assert route.call_count == 1
    request = route.calls[0].request
    assert request.headers["x-goog-api-key"] == "gem-key"
    body = json.loads(request.content)
    assert body["tools"] == [{"google_search": {}}]
    assert "responseMimeType" not in body.get("generationConfig", {})
    assert "responseSchema" not in body.get("generationConfig", {})
    user_text = body["contents"][0]["parts"][0]["text"]
    assert "Acme Corp" in user_text

    assert len(findings) == 2
    assert findings[0] == ScoutFinding(
        topic="Overview", summary="A mid-size vendor.", source_url="https://acme.example/about"
    )
    # Missing source_url backfilled from the first unused grounding chunk uri.
    assert findings[1].topic == "Products"
    assert findings[1].source_url == "https://news.example/acme-1"
    assert state.scout_findings == findings


@respx.mock
async def test_http_500_returns_empty_and_does_not_raise(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_native_base_url=NATIVE_BASE_URL)
    respx.post(model_url("gemini-flash-latest")).mock(return_value=httpx.Response(500))

    state = make_state()
    findings = await run(state)

    assert findings == []
    assert state.scout_findings == []


@respx.mock
async def test_timeout_returns_empty_and_does_not_raise(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_native_base_url=NATIVE_BASE_URL)
    respx.post(model_url("gemini-flash-latest")).mock(side_effect=httpx.TimeoutException("timed out"))

    state = make_state()
    findings = await run(state)

    assert findings == []


@respx.mock
async def test_unparsable_text_returns_empty(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_native_base_url=NATIVE_BASE_URL)
    respx.post(model_url("gemini-flash-latest")).mock(
        return_value=httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "not json at all"}]}}]}
        )
    )

    state = make_state()
    findings = await run(state)

    assert findings == []


@respx.mock
async def test_malformed_json_then_valid_retries_once_and_returns_findings(patch_settings, caplog):
    patch_settings(gemini_api_key="gem-key", gemini_native_base_url=NATIVE_BASE_URL)
    route = respx.post(model_url("gemini-flash-latest")).mock(
        side_effect=[
            httpx.Response(
                200, json={"candidates": [{"content": {"parts": [{"text": "not json at all"}]}}]}
            ),
            gemini_response(None),
        ]
    )

    with caplog.at_level(logging.WARNING, logger="app.services.scout_agent"):
        state = make_state()
        findings = await run(state)

    assert route.call_count == 2
    assert len(findings) == 2
    assert "retrying once" in caplog.text


@respx.mock
async def test_malformed_json_twice_soft_fails_with_warning(patch_settings, caplog):
    patch_settings(gemini_api_key="gem-key", gemini_native_base_url=NATIVE_BASE_URL)
    route = respx.post(model_url("gemini-flash-latest")).mock(
        return_value=httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "not json at all"}]}}]}
        )
    )

    with caplog.at_level(logging.WARNING, logger="app.services.scout_agent"):
        state = make_state()
        findings = await run(state)

    assert route.call_count == 2
    assert findings == []
    assert "retrying once" in caplog.text


@respx.mock
async def test_empty_company_name_makes_zero_http_calls(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_native_base_url=NATIVE_BASE_URL)
    route = respx.post(model_url("gemini-flash-latest")).mock(return_value=gemini_response(None))

    state = make_state(company_name="   ")
    findings = await run(state)

    assert findings == []
    assert state.scout_findings == []
    assert route.call_count == 0


@respx.mock
async def test_scout_disabled_makes_zero_http_calls(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_native_base_url=NATIVE_BASE_URL, scout_enabled=False)
    route = respx.post(model_url("gemini-flash-latest")).mock(return_value=gemini_response(None))

    state = make_state()
    findings = await run(state)

    assert findings == []
    assert route.call_count == 0


@respx.mock
async def test_model_404_retries_once_with_fallback(patch_settings):
    patch_settings(
        gemini_api_key="gem-key",
        gemini_native_base_url=NATIVE_BASE_URL,
        gemini_model="gemini-flash-latest",
        gemini_model_fallback="gemini-3.5-flash",
    )
    primary_route = respx.post(model_url("gemini-flash-latest")).mock(return_value=httpx.Response(404))
    fallback_route = respx.post(model_url("gemini-3.5-flash")).mock(return_value=gemini_response(None))

    state = make_state()
    findings = await run(state)

    assert primary_route.call_count == 1
    assert fallback_route.call_count == 1
    assert len(findings) == 2


@respx.mock
async def test_empty_findings_array_is_valid(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_native_base_url=NATIVE_BASE_URL)
    respx.post(model_url("gemini-flash-latest")).mock(return_value=gemini_response([]))

    state = make_state()
    findings = await run(state)

    assert findings == []


@respx.mock
async def test_entries_missing_topic_or_summary_are_skipped(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_native_base_url=NATIVE_BASE_URL)
    respx.post(model_url("gemini-flash-latest")).mock(
        return_value=gemini_response(
            [
                {"topic": "Overview", "summary": "Fine.", "source_url": None},
                {"topic": "", "summary": "Missing topic.", "source_url": None},
                {"topic": "No summary", "summary": "", "source_url": None},
            ]
        )
    )

    state = make_state()
    findings = await run(state)

    assert len(findings) == 1
    assert findings[0].topic == "Overview"


@respx.mock
async def test_malformed_grounding_metadata_does_not_crash_scout(patch_settings):
    # groundingMetadata's shape isn't a stable contract; a shape change there
    # (e.g. groundingChunks is a string, not a list of dicts) must not break
    # the rest of parsing - only backfilling is skipped.
    patch_settings(gemini_api_key="gem-key", gemini_native_base_url=NATIVE_BASE_URL)
    body = {"findings": [{"topic": "Overview", "summary": "Fine.", "source_url": None}]}
    candidate = {
        "content": {"parts": [{"text": json.dumps(body)}]},
        "groundingMetadata": {"groundingChunks": "not-a-list"},
    }
    respx.post(model_url("gemini-flash-latest")).mock(
        return_value=httpx.Response(200, json={"candidates": [candidate]})
    )

    state = make_state()
    findings = await run(state)

    assert len(findings) == 1
    assert findings[0].source_url is None


@respx.mock
async def test_findings_value_not_a_list_returns_empty(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_native_base_url=NATIVE_BASE_URL)
    respx.post(model_url("gemini-flash-latest")).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": json.dumps({"findings": "not a list"})}]}}]
            },
        )
    )

    state = make_state()
    findings = await run(state)

    assert findings == []


@respx.mock
async def test_non_dict_entries_in_findings_are_skipped(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_native_base_url=NATIVE_BASE_URL)
    respx.post(model_url("gemini-flash-latest")).mock(
        return_value=gemini_response(["just a string", {"topic": "Overview", "summary": "Fine.", "source_url": None}])
    )

    state = make_state()
    findings = await run(state)

    assert len(findings) == 1
    assert findings[0].topic == "Overview"


@respx.mock
async def test_custom_provider_is_used_instead_of_default(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_native_base_url=NATIVE_BASE_URL)

    class StubProvider:
        async def research(self, company_name):
            return [ScoutFinding(topic="stub", summary="from stub provider", source_url=None)]

    state = make_state()
    findings = await run(state, provider=StubProvider())

    assert findings == [ScoutFinding(topic="stub", summary="from stub provider", source_url=None)]
    assert state.scout_findings == findings


# scout_agent must be listed in conftest._SETTINGS_IMPORTERS or patch_settings
# silently won't reach it; this guards against that regression.
def test_scout_agent_settings_are_patchable(patch_settings):
    patched = patch_settings(gemini_api_key="sentinel-key")
    assert scout_agent.settings is patched


class TestGeminiSearchProviderDirect:
    @respx.mock
    async def test_research_returns_findings(self, patch_settings):
        patch_settings(gemini_api_key="gem-key", gemini_native_base_url=NATIVE_BASE_URL)
        respx.post(model_url("gemini-flash-latest")).mock(return_value=gemini_response(None))

        provider = GeminiSearchProvider()
        findings = await provider.research("Acme Corp")

        assert len(findings) == 2

    @respx.mock
    async def test_research_raises_on_http_error(self, patch_settings):
        patch_settings(gemini_api_key="gem-key", gemini_native_base_url=NATIVE_BASE_URL)
        respx.post(model_url("gemini-flash-latest")).mock(return_value=httpx.Response(500))

        provider = GeminiSearchProvider()
        with pytest.raises(httpx.HTTPStatusError):
            await provider.research("Acme Corp")
