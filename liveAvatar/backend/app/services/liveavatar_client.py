"""The entire LiveAvatar (HeyGen) HTTP surface, as thin async wrappers around
`httpx`. Nothing here holds state - each call opens its own client, hits one
LiveAvatar REST endpoint under `settings.liveavatar_base_url`, and returns.

These calls are orchestrated from `app.routers.sessions`, which per session
provisions the four LiveAvatar resources (context, secret, LLM configuration,
session token) and tears the disposable ones down afterward. In gateway
("FULL") mode the LLM configuration points the avatar's Custom LLM at our own
`/llm/{interview_id}/v1` endpoint, so conversation logic runs in our process.

Two distinct error contracts live side by side here, so callers can rely on
them: the *create* calls and `create_session_token` call `raise_for_status()`
and surface the created resource id (provisioning must fail loudly, since a
half-provisioned session is unusable), while the *delete* calls are
deliberately fire-and-forget - they never `raise_for_status()`, so best-effort
teardown of an already-gone or slow-to-delete resource never breaks the
caller's cleanup path. `stop_session` is the odd one out: it returns the raw
`httpx.Response` unexamined for the caller to inspect."""

import uuid

import httpx

from app.config import settings


async def create_context(api_key: str, full_prompt: str, opening_text: str) -> str:
    """Create a LiveAvatar persona *context* (POST /contexts) and return its id.

    The context carries the avatar's system prompt and the opening line it
    speaks when a session starts; `full_prompt` is truncated to 25000 chars to
    stay within the endpoint's limits. Calls `raise_for_status()`, so a failed
    create propagates rather than yielding an unusable context id."""
    unique_name = f"AI Interviewer w/ Context {uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient() as client:
        context_res = await client.post(
            f"{settings.liveavatar_base_url}/contexts",
            json={
                "name": unique_name,
                "prompt": full_prompt[:25000],  # Keep within reasonable limits
                "opening_text": opening_text,
            },
            headers={"X-API-KEY": api_key},
        )
        context_res.raise_for_status()
        return context_res.json()["data"]["id"]


async def delete_context(api_key: str, context_id: str) -> None:
    """Delete a persona context (DELETE /contexts/{id}) as post-session cleanup.

    Fire-and-forget: the response is not checked and `raise_for_status()` is
    never called, so a missing or slow-to-delete context can't break teardown."""
    async with httpx.AsyncClient() as client:
        await client.delete(
            f"{settings.liveavatar_base_url}/contexts/{context_id}",
            headers={"X-API-KEY": api_key},
        )


async def create_llm_secret(api_key: str, value: str) -> str:
    """Store the gateway's API key as a LiveAvatar *secret* (POST /secrets) and
    return its id, to be referenced by `create_llm_configuration`.

    `value` is the bearer token HeyGen will send back to our gateway. The
    Secrets API has no generic LLM key type, so custom OpenAI-compatible
    endpoints must register under `OPENAI_API_KEY` (see the inline note and
    docs/llm-gateway-notes.md). Calls `raise_for_status()`."""
    async with httpx.AsyncClient() as client:
        secret_res = await client.post(
            f"{settings.liveavatar_base_url}/secrets",
            json={
                # The Secrets API has no LLM_API_KEY type - custom
                # OpenAI-compatible endpoints must use OPENAI_API_KEY
                # (Phase 0 spike finding, see docs/llm-gateway-notes.md).
                "secret_type": "OPENAI_API_KEY",
                "secret_value": value,
                "secret_name": f"Resonance Gateway {uuid.uuid4().hex[:8]}",
            },
            headers={"X-API-KEY": api_key},
        )
        secret_res.raise_for_status()
        return secret_res.json()["data"]["id"]


async def create_llm_configuration(api_key: str, secret_id: str, base_url: str) -> str:
    """Register a Custom LLM configuration (POST /llm-configurations) pointing
    the avatar at our own gateway, and return its id.

    This is what makes FULL/gateway mode work: `base_url` is our public
    `/llm/{interview_id}/v1` callback and `secret_id` is the secret from
    `create_llm_secret`, so HeyGen calls back into our Host agent for every
    turn. Calls `raise_for_status()`, then returns `id` (falling back to
    `llm_configuration_id` for API-shape variance)."""
    async with httpx.AsyncClient() as client:
        llm_res = await client.post(
            f"{settings.liveavatar_base_url}/llm-configurations",
            json={
                "display_name": f"Resonance Host {uuid.uuid4().hex[:8]}",
                "model_name": "resonance-host",
                "secret_id": secret_id,
                "base_url": base_url,
            },
            headers={"X-API-KEY": api_key},
        )
        llm_res.raise_for_status()
        data = llm_res.json()["data"]
        # Use "id" or fallback if "llm_configuration_id" isn't present
        return data.get("id") or data.get("llm_configuration_id")


async def delete_llm_configuration(api_key: str, config_id: str) -> None:
    """Delete a Custom LLM configuration (DELETE /llm-configurations/{id}) as
    post-session cleanup.

    Fire-and-forget: the response is not checked and `raise_for_status()` is
    never called, so teardown can't fail on an already-gone configuration."""
    async with httpx.AsyncClient() as client:
        await client.delete(
            f"{settings.liveavatar_base_url}/llm-configurations/{config_id}",
            headers={"X-API-KEY": api_key},
        )


async def delete_secret(api_key: str, secret_id: str) -> None:
    """Delete a stored secret (DELETE /secrets/{id}) as post-session cleanup.

    Fire-and-forget: the response is not checked and `raise_for_status()` is
    never called, so teardown can't fail on an already-gone secret."""
    async with httpx.AsyncClient() as client:
        await client.delete(
            f"{settings.liveavatar_base_url}/secrets/{secret_id}",
            headers={"X-API-KEY": api_key},
        )


async def create_session_token(
    api_key: str,
    llm_configuration_id: str | None,
    context_id: str | None,
    *,
    avatar_id: str | None = None,
    is_sandbox: bool = True,
    voice_id: str | None = None,
    max_session_duration: int | None = None,
) -> dict:
    """Mint a FULL-mode session token (POST /sessions/token) binding the avatar
    to this interview's provisioned resources, and return the `data` block
    (session token, LiveKit connection info, etc.).

    Always requests `mode: "FULL"` with English persona. `avatar_id` defaults
    to `settings.avatar_id`; `is_sandbox` must match the tier (True pins the
    free sandbox avatar - pairing the sandbox avatar with False makes LiveKit
    time out, see docs/KT.md). The optional `llm_configuration_id`, `context_id`,
    `voice_id`, and `max_session_duration` (prod-tier cap) are only added when
    provided. Calls `raise_for_status()`, so a bad token request propagates."""
    token_payload = {
        "mode": "FULL",
        "avatar_id": avatar_id or settings.avatar_id,
        "is_sandbox": is_sandbox,
        "avatar_persona": {"language": "en"},
    }

    if llm_configuration_id:
        token_payload["llm_configuration_id"] = llm_configuration_id

    if context_id:
        token_payload["avatar_persona"]["context_id"] = context_id

    if voice_id:
        token_payload["avatar_persona"]["voice_id"] = voice_id

    if max_session_duration is not None:
        token_payload["max_session_duration"] = max_session_duration

    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            f"{settings.liveavatar_base_url}/sessions/token",
            json=token_payload,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        )
        token_response.raise_for_status()
        return token_response.json()["data"]


async def stop_session(session_token: str) -> httpx.Response:
    """Ask LiveAvatar to end an active session (POST /sessions/stop), authorized
    by the session's own bearer token rather than the account API key.

    Returns the raw `httpx.Response` without checking it or calling
    `raise_for_status()` - the caller decides how to interpret the outcome
    (e.g. a session HeyGen has already ended on its own is not an error)."""
    async with httpx.AsyncClient() as client:
        return await client.post(
            f"{settings.liveavatar_base_url}/sessions/stop",
            headers={"Authorization": f"Bearer {session_token}"},
        )
