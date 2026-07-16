import httpx
import pytest
import respx

from app.services import liveavatar_client

BASE_URL = "https://api.liveavatar.com/v1"


@respx.mock
async def test_create_context_returns_id(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL)
    route = respx.post(f"{BASE_URL}/contexts").mock(
        return_value=httpx.Response(200, json={"data": {"id": "ctx-123"}})
    )
    context_id = await liveavatar_client.create_context("api-key", "some prompt")
    assert context_id == "ctx-123"
    assert route.called
    sent_request = route.calls[0].request
    assert sent_request.headers["x-api-key"] == "api-key"


@respx.mock
async def test_create_context_truncates_long_prompt(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/contexts").mock(
        return_value=httpx.Response(200, json={"data": {"id": "ctx-123"}})
    )
    long_prompt = "x" * 30000
    await liveavatar_client.create_context("api-key", long_prompt)
    sent_body = respx.calls[0].request.content
    import json

    parsed = json.loads(sent_body)
    assert len(parsed["prompt"]) == 25000


@respx.mock
async def test_create_context_raises_on_http_error(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/contexts").mock(return_value=httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(httpx.HTTPStatusError):
        await liveavatar_client.create_context("api-key", "prompt")


@respx.mock
async def test_delete_context_does_not_raise_on_error(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL)
    route = respx.delete(f"{BASE_URL}/contexts/ctx-1").mock(return_value=httpx.Response(500))
    # Should not raise even though the response is a server error - delete_context
    # never calls raise_for_status (fire-and-forget cleanup).
    await liveavatar_client.delete_context("api-key", "ctx-1")
    assert route.called


@respx.mock
async def test_create_session_token_without_llm_or_context(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL, avatar_id="avatar-1")
    route = respx.post(f"{BASE_URL}/sessions/token").mock(
        return_value=httpx.Response(
            200, json={"data": {"session_token": "tok", "session_id": "sid"}}
        )
    )
    result = await liveavatar_client.create_session_token("api-key", None, None, None)
    assert result == {"session_token": "tok", "session_id": "sid"}
    assert route.call_count == 1
    body = route.calls[0].request.content
    import json

    parsed = json.loads(body)
    assert parsed["is_sandbox"] is True
    assert parsed["avatar_id"] == "avatar-1"
    assert "llm_configuration_id" not in parsed
    assert "context_id" not in parsed["avatar_persona"]


@respx.mock
async def test_create_session_token_with_llm_and_context(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL)
    route = respx.post(f"{BASE_URL}/sessions/token").mock(
        return_value=httpx.Response(
            200, json={"data": {"session_token": "tok", "session_id": "sid"}}
        )
    )
    await liveavatar_client.create_session_token("api-key", "llm-1", "ctx-1", "llm-1")
    import json

    body = json.loads(route.calls[0].request.content)
    assert body["llm_configuration_id"] == "llm-1"
    assert body["avatar_persona"]["context_id"] == "ctx-1"


@respx.mock
async def test_create_session_token_is_sandbox_always_true(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL)
    route = respx.post(f"{BASE_URL}/sessions/token").mock(
        return_value=httpx.Response(
            200, json={"data": {"session_token": "tok", "session_id": "sid"}}
        )
    )
    await liveavatar_client.create_session_token("api-key", "llm-1", "ctx-1", "llm-1")
    import json

    body = json.loads(route.calls[0].request.content)
    assert body["is_sandbox"] is True


@respx.mock
async def test_create_session_token_gemini_fallback_to_heygen(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL)
    route = respx.post(f"{BASE_URL}/sessions/token")
    route.side_effect = [
        httpx.Response(500, json={"error": "gemini config invalid"}),
        httpx.Response(200, json={"data": {"session_token": "tok", "session_id": "sid"}}),
    ]

    result = await liveavatar_client.create_session_token(
        "api-key", "gemini-llm-id", "ctx-1", "gemini-llm-id"
    )

    assert result == {"session_token": "tok", "session_id": "sid"}
    assert route.call_count == 2
    import json

    second_body = json.loads(route.calls[1].request.content)
    assert "llm_configuration_id" not in second_body
    # context should still be present on the retry
    assert second_body["avatar_persona"]["context_id"] == "ctx-1"


@respx.mock
async def test_create_session_token_no_fallback_without_gemini_id(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL)
    route = respx.post(f"{BASE_URL}/sessions/token").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        await liveavatar_client.create_session_token("api-key", "custom-llm-id", None, None)
    assert route.call_count == 1


@respx.mock
async def test_create_session_token_fallback_also_fails(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL)
    route = respx.post(f"{BASE_URL}/sessions/token")
    route.side_effect = [
        httpx.Response(500, json={"error": "first failure"}),
        httpx.Response(500, json={"error": "second failure"}),
    ]
    with pytest.raises(httpx.HTTPStatusError):
        await liveavatar_client.create_session_token(
            "api-key", "gemini-llm-id", None, "gemini-llm-id"
        )
    assert route.call_count == 2


@respx.mock
async def test_stop_session_does_not_raise_on_error(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL)
    route = respx.post(f"{BASE_URL}/sessions/stop").mock(return_value=httpx.Response(500))
    # stop_session never calls raise_for_status - fire-and-forget.
    response = await liveavatar_client.stop_session("session-token")
    assert response.status_code == 500
    assert route.calls[0].request.headers["authorization"] == "Bearer session-token"
