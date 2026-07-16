import httpx
import respx

from app.services import gemini_provisioning
from app.services.session_state import active_sessions

BASE_URL = "https://api.liveavatar.com/v1"


@respx.mock
def test_create_session_happy_path(client, patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/sessions/token").mock(
        return_value=httpx.Response(
            200, json={"data": {"session_token": "tok", "session_id": "sid"}}
        )
    )

    response = client.post("/api/session", json={})

    assert response.status_code == 200
    assert response.json() == {"session_token": "tok", "session_id": "sid"}
    assert active_sessions.count == 1


@respx.mock
def test_create_session_gemini_fallback_to_heygen(client, patch_settings, monkeypatch):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)
    monkeypatch.setattr(
        gemini_provisioning, "get_gemini_llm_configuration_id", lambda: "gemini-llm-1"
    )

    route = respx.post(f"{BASE_URL}/sessions/token")
    route.side_effect = [
        httpx.Response(500, json={"error": "gemini invalid"}),
        httpx.Response(200, json={"data": {"session_token": "tok", "session_id": "sid"}}),
    ]

    response = client.post("/api/session", json={})

    assert response.status_code == 200
    assert route.call_count == 2
    import json

    second_body = json.loads(route.calls[1].request.content)
    assert "llm_configuration_id" not in second_body


@respx.mock
def test_create_session_missing_api_key_collapses_to_500(client, patch_settings):
    # The inner HTTPException("LiveAvatar API Key not configured") is caught by
    # the outer generic `except Exception` and collapsed to a plain 500 -
    # documented existing behavior (see code comment in sessions.py).
    patch_settings(liveavatar_api_key=None)

    response = client.post("/api/session", json={})

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal Server Error"
    assert len(respx.calls) == 0


@respx.mock
def test_create_session_liveavatar_http_error_passthrough(client, patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/sessions/token").mock(
        return_value=httpx.Response(503, json={"error": "down"})
    )

    response = client.post("/api/session", json={})

    assert response.status_code == 503
    assert response.json()["detail"] == "Failed to create or start session"
    assert active_sessions.count == 0


@respx.mock
def test_stop_session_missing_token_is_ignored(client, patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)

    response = client.post("/api/session/stop", json={})

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert len(respx.calls) == 0


@respx.mock
def test_stop_session_happy_path_decrements_counter(client, patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)
    active_sessions.increment()
    respx.post(f"{BASE_URL}/sessions/stop").mock(return_value=httpx.Response(200))

    response = client.post("/api/session/stop", json={"session_token": "tok"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "stopped"
    assert body["api_status"] == 200
    assert active_sessions.count == 0


@respx.mock
def test_stop_session_context_cleanup_failure_swallowed(client, patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/sessions/stop").mock(return_value=httpx.Response(200))
    respx.delete(f"{BASE_URL}/contexts/ctx-1").mock(side_effect=httpx.ConnectError("boom"))

    response = client.post(
        "/api/session/stop", json={"session_token": "tok", "context_id": "ctx-1"}
    )

    # Context cleanup failure is logged, not propagated - still 200.
    assert response.status_code == 200
    assert response.json()["status"] == "stopped"


@respx.mock
def test_stop_session_cleans_up_context_when_provided(client, patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/sessions/stop").mock(return_value=httpx.Response(200))
    delete_route = respx.delete(f"{BASE_URL}/contexts/ctx-1").mock(return_value=httpx.Response(200))

    response = client.post(
        "/api/session/stop", json={"session_token": "tok", "context_id": "ctx-1"}
    )

    assert response.status_code == 200
    assert delete_route.called


@respx.mock
def test_stop_session_no_context_cleanup_without_api_key(client, patch_settings):
    patch_settings(liveavatar_api_key=None, liveavatar_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/sessions/stop").mock(return_value=httpx.Response(200))

    response = client.post(
        "/api/session/stop", json={"session_token": "tok", "context_id": "ctx-1"}
    )

    assert response.status_code == 200
    # No delete call should have been attempted since there's no api key.
    assert not any("contexts" in str(call.request.url) for call in respx.calls)


@respx.mock
def test_stop_session_call_failure_returns_500(client, patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/sessions/stop").mock(side_effect=httpx.ConnectError("boom"))

    response = client.post("/api/session/stop", json={"session_token": "tok"})

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to stop session"
