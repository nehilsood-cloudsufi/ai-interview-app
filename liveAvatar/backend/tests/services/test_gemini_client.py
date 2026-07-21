import json
import logging

import httpx
import pytest
import respx

from app.services import gemini_client
from app.services.gemini_client import chat_completion, stream_chat_completion

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
CHAT_URL = f"{GEMINI_BASE_URL}chat/completions"

OK_BODY = {"choices": [{"message": {"content": "hello"}}]}


def make_payload(model: str = "gemini-flash-latest") -> dict:
    return {"model": model, "messages": [{"role": "user", "content": "hi"}]}


@respx.mock
async def test_happy_path_single_call(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=OK_BODY))

    data = await chat_completion(make_payload(), timeout=8.0, fallback_model="gemini-3.5-flash")

    assert data == OK_BODY
    assert route.call_count == 1
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer gem-key"
    assert json.loads(request.content)["model"] == "gemini-flash-latest"


@respx.mock
async def test_model_404_retries_once_with_fallback_model(patch_settings, caplog):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            httpx.Response(404, json={"error": {"message": "models/gemini-flash-latest is not found"}}),
            httpx.Response(200, json=OK_BODY),
        ]
    )

    with caplog.at_level(logging.WARNING, logger="app.services.gemini_client"):
        data = await chat_completion(make_payload(), timeout=8.0, fallback_model="gemini-3.5-flash")

    assert data == OK_BODY
    assert route.call_count == 2
    assert json.loads(route.calls[0].request.content)["model"] == "gemini-flash-latest"
    assert json.loads(route.calls[1].request.content)["model"] == "gemini-3.5-flash"
    assert "gemini-flash-latest" in caplog.text
    assert "gemini-3.5-flash" in caplog.text


@respx.mock
async def test_400_mentioning_model_triggers_fallback(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            httpx.Response(400, json={"error": {"message": "unknown model requested"}}),
            httpx.Response(200, json=OK_BODY),
        ]
    )

    data = await chat_completion(make_payload(), timeout=8.0, fallback_model="gemini-3.5-flash")

    assert data == OK_BODY
    assert route.call_count == 2


@respx.mock
async def test_400_not_mentioning_model_raises_without_fallback(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad request: invalid temperature"}})
    )

    with pytest.raises(httpx.HTTPStatusError):
        await chat_completion(make_payload(), timeout=8.0, fallback_model="gemini-3.5-flash")

    assert route.call_count == 1


@respx.mock
async def test_5xx_raises_without_fallback(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        await chat_completion(make_payload(), timeout=8.0, fallback_model="gemini-3.5-flash")

    assert route.call_count == 1


@respx.mock
async def test_fallback_also_failing_raises(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(404, json={"error": {"message": "not found"}}))

    with pytest.raises(httpx.HTTPStatusError):
        await chat_completion(make_payload(), timeout=8.0, fallback_model="gemini-3.5-flash")

    # Exactly one fallback retry - never a loop.
    assert route.call_count == 2


@respx.mock
async def test_no_fallback_model_raises_immediately_on_404(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(404))

    with pytest.raises(httpx.HTTPStatusError):
        await chat_completion(make_payload(), timeout=8.0)

    assert route.call_count == 1


@respx.mock
async def test_fallback_equal_to_primary_not_retried(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(404))

    with pytest.raises(httpx.HTTPStatusError):
        await chat_completion(
            make_payload(model="gemini-3.5-flash"), timeout=8.0, fallback_model="gemini-3.5-flash"
        )

    assert route.call_count == 1


# --- streaming ---------------------------------------------------------------


def sse(*contents: str, done: bool = True) -> str:
    lines = [f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}\n\n" for c in contents]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines)


async def _collect(payload: dict, **kwargs) -> list[str]:
    return [delta async for delta in stream_chat_completion(payload, **kwargs)]


@respx.mock
async def test_stream_yields_content_deltas(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, text=sse("Hello", " there", " friend")))

    deltas = await _collect(make_payload(), timeout=8.0, fallback_model="gemini-3.5-flash")

    assert deltas == ["Hello", " there", " friend"]
    assert route.call_count == 1
    body = json.loads(route.calls[0].request.content)
    assert body["stream"] is True
    assert route.calls[0].request.headers["authorization"] == "Bearer gem-key"


@respx.mock
async def test_stream_stops_at_done_sentinel(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    # Content after [DONE] must be ignored.
    body = sse("A", "B") + sse("C")
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, text=body))

    deltas = await _collect(make_payload(), timeout=8.0)

    assert deltas == ["A", "B"]


@respx.mock
async def test_stream_skips_blank_and_keepalive_lines(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    body = ": keepalive\n\n" + "\n\n" + sse("only")
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, text=body))

    deltas = await _collect(make_payload(), timeout=8.0)

    assert deltas == ["only"]


@respx.mock
async def test_stream_model_404_retries_once_with_fallback(patch_settings, caplog):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            httpx.Response(404, json={"error": {"message": "models/gemini-flash-latest is not found"}}),
            httpx.Response(200, text=sse("recovered")),
        ]
    )

    with caplog.at_level(logging.WARNING, logger="app.services.gemini_client"):
        deltas = await _collect(make_payload(), timeout=8.0, fallback_model="gemini-3.5-flash")

    assert deltas == ["recovered"]
    assert route.call_count == 2
    assert json.loads(route.calls[1].request.content)["model"] == "gemini-3.5-flash"
    assert json.loads(route.calls[1].request.content)["stream"] is True


@respx.mock
async def test_stream_400_mentioning_model_triggers_fallback(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            httpx.Response(400, json={"error": {"message": "unknown model requested"}}),
            httpx.Response(200, text=sse("ok")),
        ]
    )

    deltas = await _collect(make_payload(), timeout=8.0, fallback_model="gemini-3.5-flash")

    assert deltas == ["ok"]
    assert route.call_count == 2


@respx.mock
async def test_stream_400_not_model_raises_before_yielding(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(400, json={"error": {"message": "invalid temperature"}})
    )

    with pytest.raises(httpx.HTTPStatusError):
        await _collect(make_payload(), timeout=8.0, fallback_model="gemini-3.5-flash")

    assert route.call_count == 1


@respx.mock
async def test_stream_5xx_raises(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        await _collect(make_payload(), timeout=8.0, fallback_model="gemini-3.5-flash")


# gemini_client must be listed in conftest._SETTINGS_IMPORTERS or
# patch_settings silently won't reach it; this guards against that regression.
def test_gemini_client_settings_are_patchable(patch_settings):
    patched = patch_settings(gemini_api_key="sentinel-key")
    assert gemini_client.settings is patched
