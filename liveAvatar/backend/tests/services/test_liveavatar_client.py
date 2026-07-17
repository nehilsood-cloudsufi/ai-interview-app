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
async def test_create_llm_secret_returns_id(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL)
    route = respx.post(f"{BASE_URL}/secrets").mock(
        return_value=httpx.Response(200, json={"data": {"id": "sec-1"}})
    )
    secret_id = await liveavatar_client.create_llm_secret("api-key", "gw-token")
    assert secret_id == "sec-1"
    sent_request = route.calls[0].request
    assert sent_request.headers["x-api-key"] == "api-key"
    import json

    body = json.loads(sent_request.content)
    # CRITICAL: the Secrets API has no LLM_API_KEY type - custom
    # OpenAI-compatible endpoints must use OPENAI_API_KEY (spike finding,
    # see liveAvatar/docs/llm-gateway-notes.md).
    assert body["secret_type"] == "OPENAI_API_KEY"
    assert body["secret_value"] == "gw-token"
    assert body["secret_name"].startswith("Resonance Gateway ")


@respx.mock
async def test_create_llm_secret_raises_on_http_error(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/secrets").mock(return_value=httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(httpx.HTTPStatusError):
        await liveavatar_client.create_llm_secret("api-key", "gw-token")


@respx.mock
async def test_create_llm_configuration_returns_id(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL)
    route = respx.post(f"{BASE_URL}/llm-configurations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "llm-1"}})
    )
    config_id = await liveavatar_client.create_llm_configuration(
        "api-key", "sec-1", "https://example.com/llm/abc/v1"
    )
    assert config_id == "llm-1"
    sent_request = route.calls[0].request
    assert sent_request.headers["x-api-key"] == "api-key"
    import json

    body = json.loads(sent_request.content)
    assert body["model_name"] == "resonance-host"
    assert body["secret_id"] == "sec-1"
    assert body["base_url"] == "https://example.com/llm/abc/v1"
    assert body["display_name"].startswith("Resonance Host ")


@respx.mock
async def test_create_llm_configuration_falls_back_to_llm_configuration_id(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/llm-configurations").mock(
        return_value=httpx.Response(200, json={"data": {"llm_configuration_id": "llm-2"}})
    )
    config_id = await liveavatar_client.create_llm_configuration(
        "api-key", "sec-1", "https://example.com/llm/abc/v1"
    )
    assert config_id == "llm-2"


@respx.mock
async def test_create_llm_configuration_raises_on_http_error(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/llm-configurations").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        await liveavatar_client.create_llm_configuration(
            "api-key", "sec-1", "https://example.com/llm/abc/v1"
        )


@respx.mock
async def test_delete_llm_configuration_does_not_raise_on_error(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL)
    route = respx.delete(f"{BASE_URL}/llm-configurations/llm-1").mock(
        return_value=httpx.Response(500)
    )
    # Fire-and-forget cleanup, same contract as delete_context.
    await liveavatar_client.delete_llm_configuration("api-key", "llm-1")
    assert route.called
    assert route.calls[0].request.headers["x-api-key"] == "api-key"


@respx.mock
async def test_delete_secret_does_not_raise_on_error(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL)
    route = respx.delete(f"{BASE_URL}/secrets/sec-1").mock(return_value=httpx.Response(500))
    # Fire-and-forget cleanup, same contract as delete_context.
    await liveavatar_client.delete_secret("api-key", "sec-1")
    assert route.called
    assert route.calls[0].request.headers["x-api-key"] == "api-key"


@respx.mock
async def test_stop_session_does_not_raise_on_error(patch_settings):
    patch_settings(liveavatar_base_url=BASE_URL)
    route = respx.post(f"{BASE_URL}/sessions/stop").mock(return_value=httpx.Response(500))
    # stop_session never calls raise_for_status - fire-and-forget.
    response = await liveavatar_client.stop_session("session-token")
    assert response.status_code == 500
    assert route.calls[0].request.headers["authorization"] == "Bearer session-token"
