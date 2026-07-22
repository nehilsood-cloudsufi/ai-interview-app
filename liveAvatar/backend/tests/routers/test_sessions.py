import httpx
import respx

from app.services import interview_state
from app.services.session_state import active_sessions

BASE_URL = "https://api.liveavatar.com/v1"
PUBLIC_URL = "https://resonance.example.com"


def _seed_interview(tier="dev") -> interview_state.InterviewState:
    return interview_state.create(
        interview_state.VendorProfile(
            company_name="Acme",
            contact_name="Jo",
            contact_role=None,
        ),
        "ai_ml",
        tier,
    )


@respx.mock
def test_create_session_missing_interview_id_returns_400(client, patch_settings):
    patch_settings(
        liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL, public_base_url=PUBLIC_URL
    )

    response = client.post("/api/session", json={})

    assert response.status_code == 400
    assert response.json()["detail"] == "interview_id is required"
    assert len(respx.calls) == 0


@respx.mock
def test_create_session_missing_public_base_url_returns_503(client, patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL, public_base_url=None)
    state = _seed_interview()

    response = client.post("/api/session", json={"interview_id": state.interview_id})

    assert response.status_code == 503
    assert response.json()["detail"] == "PUBLIC_BASE_URL is not configured"
    assert len(respx.calls) == 0


@respx.mock
def test_create_session_prod_tier_unconfigured_returns_503(client, patch_settings):
    # A prod-tier interview must never silently fall back to the sandbox
    # avatar: if PROD_AVATAR_ID disappeared since interview creation, fail
    # fast before any LiveAvatar resource is provisioned.
    patch_settings(
        liveavatar_api_key="live-key",
        liveavatar_base_url=BASE_URL,
        public_base_url=PUBLIC_URL,
        prod_avatar_id=None,
    )
    state = _seed_interview(tier="prod")

    response = client.post("/api/session", json={"interview_id": state.interview_id})

    assert response.status_code == 503
    assert "PROD_AVATAR_ID" in response.json()["detail"]
    assert len(respx.calls) == 0


@respx.mock
def test_create_session_prod_tier_token_payload(client, patch_settings):
    import json

    patch_settings(
        liveavatar_api_key="live-key",
        liveavatar_base_url=BASE_URL,
        public_base_url=PUBLIC_URL,
        prod_avatar_id="avatar-june",
        prod_voice_id="voice-amy",
        prod_max_session_seconds=600,
    )
    state = _seed_interview(tier="prod")
    state.max_session_seconds = 300  # the vendor picked 5 minutes on the start screen
    respx.post(f"{BASE_URL}/secrets").mock(
        return_value=httpx.Response(200, json={"data": {"id": "sec-1"}})
    )
    respx.post(f"{BASE_URL}/llm-configurations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "llm-1"}})
    )
    respx.post(f"{BASE_URL}/contexts").mock(
        return_value=httpx.Response(200, json={"data": {"id": "ctx-1"}})
    )
    token_route = respx.post(f"{BASE_URL}/sessions/token").mock(
        return_value=httpx.Response(
            200, json={"data": {"session_token": "tok", "session_id": "sid"}}
        )
    )

    response = client.post("/api/session", json={"interview_id": state.interview_id})

    assert response.status_code == 200
    body = json.loads(token_route.calls[0].request.content)
    assert body["avatar_id"] == "avatar-june"
    assert body["is_sandbox"] is False
    # picked 300s + prod_session_grace_seconds (60): HeyGen's cap sits past
    # the Host's own wrap-up so the closing is spoken before the cut.
    assert body["max_session_duration"] == 360
    assert body["avatar_persona"]["voice_id"] == "voice-amy"


@respx.mock
def test_create_session_dev_tier_token_payload_is_sandbox(client, patch_settings):
    import json

    patch_settings(
        liveavatar_api_key="live-key",
        liveavatar_base_url=BASE_URL,
        public_base_url=PUBLIC_URL,
        # Prod config present must NOT leak into dev-tier sessions.
        prod_avatar_id="avatar-june",
        prod_voice_id="voice-amy",
    )
    state = _seed_interview()
    respx.post(f"{BASE_URL}/secrets").mock(
        return_value=httpx.Response(200, json={"data": {"id": "sec-1"}})
    )
    respx.post(f"{BASE_URL}/llm-configurations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "llm-1"}})
    )
    respx.post(f"{BASE_URL}/contexts").mock(
        return_value=httpx.Response(200, json={"data": {"id": "ctx-1"}})
    )
    token_route = respx.post(f"{BASE_URL}/sessions/token").mock(
        return_value=httpx.Response(
            200, json={"data": {"session_token": "tok", "session_id": "sid"}}
        )
    )

    response = client.post("/api/session", json={"interview_id": state.interview_id})

    assert response.status_code == 200
    body = json.loads(token_route.calls[0].request.content)
    assert body["avatar_id"] == "dd73ea75-1218-4ef3-92ce-606d5f7fbc0a"
    assert body["is_sandbox"] is True
    assert "max_session_duration" not in body
    assert "voice_id" not in body["avatar_persona"]


@respx.mock
def test_create_session_missing_api_key_returns_500(client, patch_settings):
    patch_settings(liveavatar_api_key=None, liveavatar_base_url=BASE_URL, public_base_url=PUBLIC_URL)
    state = _seed_interview()

    response = client.post("/api/session", json={"interview_id": state.interview_id})

    assert response.status_code == 500
    assert response.json()["detail"] == "LiveAvatar API Key not configured"
    assert len(respx.calls) == 0


@respx.mock
def test_create_session_gateway_mode_happy_path(client, patch_settings):
    patch_settings(
        liveavatar_api_key="live-key",
        liveavatar_base_url=BASE_URL,
        public_base_url=f"{PUBLIC_URL}/",  # trailing slash must be stripped
    )
    state = _seed_interview()
    secret_route = respx.post(f"{BASE_URL}/secrets").mock(
        return_value=httpx.Response(200, json={"data": {"id": "sec-1"}})
    )
    llm_route = respx.post(f"{BASE_URL}/llm-configurations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "llm-1"}})
    )
    context_route = respx.post(f"{BASE_URL}/contexts").mock(
        return_value=httpx.Response(200, json={"data": {"id": "ctx-1"}})
    )
    token_route = respx.post(f"{BASE_URL}/sessions/token").mock(
        return_value=httpx.Response(
            200, json={"data": {"session_token": "tok", "session_id": "sid"}}
        )
    )

    response = client.post("/api/session", json={"interview_id": state.interview_id})

    assert response.status_code == 200
    assert response.json() == {"session_token": "tok", "session_id": "sid"}
    import json

    secret_body = json.loads(secret_route.calls[0].request.content)
    assert secret_body["secret_type"] == "OPENAI_API_KEY"
    assert secret_body["secret_value"] == state.gateway_token

    llm_body = json.loads(llm_route.calls[0].request.content)
    assert llm_body["secret_id"] == "sec-1"
    assert llm_body["model_name"] == "resonance-host"
    assert llm_body["base_url"] == f"{PUBLIC_URL}/llm/{state.interview_id}/v1"

    # Context prompt must be the minimal one-liner (it arrives verbatim as the
    # system message in gateway requests - see docs/llm-gateway-notes.md).
    context_body = json.loads(context_route.calls[0].request.content)
    assert "\n" not in context_body["prompt"]
    assert "Noor" in context_body["prompt"]

    token_body = json.loads(token_route.calls[0].request.content)
    assert token_body["llm_configuration_id"] == "llm-1"
    assert token_body["avatar_persona"]["context_id"] == "ctx-1"

    assert state.heygen_session_id == "sid"
    assert state.llm_config_id == "llm-1"
    assert state.secret_id == "sec-1"
    assert state.context_id == "ctx-1"
    assert state.status == "active"
    assert active_sessions.count == 1


@respx.mock
def test_create_session_gateway_mode_unknown_interview_404(client, patch_settings):
    patch_settings(
        liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL, public_base_url=PUBLIC_URL
    )

    response = client.post("/api/session", json={"interview_id": "nope"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown interview"
    assert len(respx.calls) == 0


@respx.mock
def test_create_session_gateway_mode_liveavatar_error_passthrough(client, patch_settings):
    patch_settings(
        liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL, public_base_url=PUBLIC_URL
    )
    state = _seed_interview()
    respx.post(f"{BASE_URL}/secrets").mock(return_value=httpx.Response(503, json={"error": "down"}))

    response = client.post("/api/session", json={"interview_id": state.interview_id})

    assert response.status_code == 503
    assert response.json()["detail"] == "Failed to create or start session"
    assert state.status == "created"
    assert active_sessions.count == 0


@respx.mock
def test_stop_session_missing_token_is_ignored(client, patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)

    response = client.post("/api/session/stop", json={})

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert len(respx.calls) == 0


@respx.mock
def test_stop_session_missing_token_ignored_even_without_api_key(client, patch_settings):
    # The no-op "ignored" path makes no network calls and needs no key, so it
    # must short-circuit before resolve_api_key() would raise.
    patch_settings(liveavatar_api_key=None, liveavatar_base_url=BASE_URL)

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
def test_stop_session_missing_api_key_returns_500(client, patch_settings):
    # Single-tenant: LIVEAVATAR_API_KEY is the only key source, so a missing
    # key is a genuine misconfiguration - resolve_api_key raises and stop
    # fails loudly rather than silently skipping cleanup.
    patch_settings(liveavatar_api_key=None, liveavatar_base_url=BASE_URL)

    response = client.post(
        "/api/session/stop", json={"session_token": "tok", "context_id": "ctx-1"}
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to stop session"
    assert len(respx.calls) == 0


@respx.mock
def test_stop_session_call_failure_returns_500(client, patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/sessions/stop").mock(side_effect=httpx.ConnectError("boom"))

    response = client.post("/api/session/stop", json={"session_token": "tok"})

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to stop session"


@respx.mock
def test_stop_session_gateway_mode_cleans_up_resources(client, patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)
    state = _seed_interview()
    state.llm_config_id = "llm-1"
    state.secret_id = "sec-1"
    state.context_id = "ctx-9"
    respx.post(f"{BASE_URL}/sessions/stop").mock(return_value=httpx.Response(200))
    llm_delete = respx.delete(f"{BASE_URL}/llm-configurations/llm-1").mock(
        return_value=httpx.Response(200)
    )
    secret_delete = respx.delete(f"{BASE_URL}/secrets/sec-1").mock(
        return_value=httpx.Response(200)
    )
    context_delete = respx.delete(f"{BASE_URL}/contexts/ctx-9").mock(
        return_value=httpx.Response(200)
    )

    response = client.post(
        "/api/session/stop",
        json={"session_token": "tok", "interview_id": state.interview_id},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
    assert llm_delete.called
    assert secret_delete.called
    assert context_delete.called
    assert state.status == "finished"


@respx.mock
def test_stop_session_gateway_cleanup_failures_swallowed(client, patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)
    state = _seed_interview()
    state.llm_config_id = "llm-1"
    state.secret_id = "sec-1"
    state.context_id = "ctx-9"
    respx.post(f"{BASE_URL}/sessions/stop").mock(return_value=httpx.Response(200))
    respx.delete(f"{BASE_URL}/llm-configurations/llm-1").mock(
        side_effect=httpx.ConnectError("boom")
    )
    secret_delete = respx.delete(f"{BASE_URL}/secrets/sec-1").mock(
        side_effect=httpx.ConnectError("boom")
    )
    context_delete = respx.delete(f"{BASE_URL}/contexts/ctx-9").mock(
        return_value=httpx.Response(200)
    )

    response = client.post(
        "/api/session/stop",
        json={"session_token": "tok", "interview_id": state.interview_id},
    )

    # Each cleanup failure is logged, not propagated - remaining cleanups still
    # run and the request still succeeds.
    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
    assert secret_delete.called
    assert context_delete.called
    assert state.status == "finished"


@respx.mock
def test_stop_session_unknown_interview_id_ignored(client, patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/sessions/stop").mock(return_value=httpx.Response(200))

    response = client.post(
        "/api/session/stop", json={"session_token": "tok", "interview_id": "nope"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
    # Only the stop call - no cleanup attempted for an unknown interview.
    assert len(respx.calls) == 1
