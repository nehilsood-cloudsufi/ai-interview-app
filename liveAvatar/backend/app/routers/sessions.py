import logging

import httpx
from fastapi import APIRouter, HTTPException

from app.config import settings
from app.dependencies import resolve_api_key
from app.models import CreateSessionRequest, StopSessionRequest
from app.services import gemini_provisioning, interview_state
from app.services.liveavatar_client import (
    create_context,
    create_llm_configuration,
    create_llm_secret,
    create_session_token,
    delete_context,
    delete_llm_configuration,
    delete_secret,
    stop_session,
)
from app.services.session_state import active_sessions

logger = logging.getLogger(__name__)

router = APIRouter()

# Spike finding (docs/llm-gateway-notes.md): this context prompt arrives
# verbatim as the system message in our gateway's chat-completions requests.
# Keep it a single neutral line - the real Host prompt is built server-side.
GATEWAY_CONTEXT_PROMPT = (
    "You are Noor, a professional vendor evaluator for Resonance. "
    "Follow the conversation naturally."
)


def _gateway_opening_text(state) -> str:
    """Spoken by the avatar the moment the session connects, before any
    gateway call. Greets the vendor by name (details come straight from the
    intake form - deliberately no verbal re-verification) and invites them to
    kick off; their first utterance then triggers the Host's first question."""
    profile = state.vendor_profile
    return (
        f"Hello {profile.contact_name}, welcome! I'm Noor, and I'll be running "
        f"today's evaluation with {profile.company_name}. Whenever you're "
        "ready, just say hello and we'll get started."
    )


async def _create_gateway_session(body: CreateSessionRequest) -> dict:
    """Gateway mode: register a per-interview Custom LLM (secret + LLM config
    pointing back at our /llm/{interview_id}/v1 endpoint) plus a minimal
    context, then create the session token with no Gemini fallback."""
    try:
        liveavatar_key = resolve_api_key(body.api_key)
        if not liveavatar_key:
            raise HTTPException(status_code=500, detail="LiveAvatar API Key not configured")

        state = interview_state.get(body.interview_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Unknown interview")

        secret_id = await create_llm_secret(liveavatar_key, state.gateway_token)
        gateway_base_url = f"{settings.public_base_url.rstrip('/')}/llm/{body.interview_id}/v1"
        llm_config_id = await create_llm_configuration(liveavatar_key, secret_id, gateway_base_url)
        context_id = await create_context(liveavatar_key, GATEWAY_CONTEXT_PROMPT, _gateway_opening_text(state))

        token_data = await create_session_token(liveavatar_key, llm_config_id, context_id, None)

        state.heygen_session_id = token_data["session_id"]
        state.llm_config_id = llm_config_id
        state.secret_id = secret_id
        state.context_id = context_id
        state.status = "active"

        active_sessions.increment()

        return {
            "session_token": token_data["session_token"],
            "session_id": token_data["session_id"],
        }

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        logger.error("LiveAvatar API Error: %s", e.response.text)
        raise HTTPException(status_code=e.response.status_code, detail="Failed to create or start session")
    except Exception as e:
        logger.error("Error: %s", str(e))
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/api/session")
async def create_session(body: CreateSessionRequest):
    if body.interview_id and settings.public_base_url:
        return await _create_gateway_session(body)

    try:
        liveavatar_key = resolve_api_key(body.api_key)
        if not liveavatar_key:
            raise HTTPException(status_code=500, detail="LiveAvatar API Key not configured")

        gemini_llm_configuration_id = gemini_provisioning.get_gemini_llm_configuration_id()
        # Override with Auto-Provisioned Gemini if available
        llm_configuration_id = gemini_llm_configuration_id or body.llm_configuration_id

        token_data = await create_session_token(
            liveavatar_key,
            llm_configuration_id,
            body.context_id,
            gemini_llm_configuration_id,
        )

        active_sessions.increment()

        return {
            "session_token": token_data["session_token"],
            "session_id": token_data["session_id"],
        }

    except httpx.HTTPStatusError as e:
        logger.error("LiveAvatar API Error: %s", e.response.text)
        raise HTTPException(status_code=e.response.status_code, detail="Failed to create or start session")
    except Exception as e:
        # Matches original behavior: any error here (including the "API key not
        # configured" HTTPException raised above) collapses to this generic 500.
        logger.error("Error: %s", str(e))
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/api/session/stop")
async def stop_session_route(body: StopSessionRequest):
    try:
        liveavatar_key = resolve_api_key(body.api_key)

        if not body.session_token:
            return {"status": "ignored"}

        res = await stop_session(body.session_token)

        active_sessions.decrement()

        # Clean up the dynamically created context if one was used
        if body.context_id and liveavatar_key:
            try:
                await delete_context(liveavatar_key, body.context_id)
            except Exception as e:
                logger.warning("Failed to clean up context %s: %s", body.context_id, e)

        # Gateway mode: best-effort teardown of the per-interview LLM config,
        # secret and context. Each failure is logged, never propagated.
        state = interview_state.get(body.interview_id) if body.interview_id else None
        if state is not None:
            if liveavatar_key and state.llm_config_id:
                try:
                    await delete_llm_configuration(liveavatar_key, state.llm_config_id)
                except Exception as e:
                    logger.warning("Failed to clean up LLM config %s: %s", state.llm_config_id, e)
            if liveavatar_key and state.secret_id:
                try:
                    await delete_secret(liveavatar_key, state.secret_id)
                except Exception as e:
                    logger.warning("Failed to clean up secret %s: %s", state.secret_id, e)
            if liveavatar_key and state.context_id:
                try:
                    await delete_context(liveavatar_key, state.context_id)
                except Exception as e:
                    logger.warning("Failed to clean up context %s: %s", state.context_id, e)
            state.status = "finished"

        return {"status": "stopped", "api_status": res.status_code}
    except Exception as e:
        logger.error("Error stopping session: %s", str(e))
        raise HTTPException(status_code=500, detail="Failed to stop session")
