import uuid

import httpx

from app.config import settings


async def create_context(api_key: str, full_prompt: str, opening_text: str) -> str:
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
    async with httpx.AsyncClient() as client:
        await client.delete(
            f"{settings.liveavatar_base_url}/contexts/{context_id}",
            headers={"X-API-KEY": api_key},
        )


async def create_llm_secret(api_key: str, value: str) -> str:
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
    async with httpx.AsyncClient() as client:
        await client.delete(
            f"{settings.liveavatar_base_url}/llm-configurations/{config_id}",
            headers={"X-API-KEY": api_key},
        )


async def delete_secret(api_key: str, secret_id: str) -> None:
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
    async with httpx.AsyncClient() as client:
        return await client.post(
            f"{settings.liveavatar_base_url}/sessions/stop",
            headers={"Authorization": f"Bearer {session_token}"},
        )
