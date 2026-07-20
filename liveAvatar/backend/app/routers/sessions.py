import logging

import httpx
from fastapi import APIRouter, HTTPException

from app.config import settings
from app.dependencies import resolve_api_key
from app.models import CreateSessionRequest, StopSessionRequest
from app.services import interview_state
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


def _gateway_opening_text() -> str:
    """Spoken by the avatar the moment the session connects, before any
    gateway call. The intake form is gone, so the vendor profile is empty at
    this point - onboarding happens conversationally via the `intro`
    questionnaire node - so this is a generic opener rather than a by-name
    greeting. Content is aligned with `intro`'s ask: name, role, company,
    and website."""
    return (
        "Hello, and welcome! I'm Noor, and I'll be running today's vendor "
        "evaluation. To get us started, could you introduce yourself - your "
        "name, your role, the company you represent, and that company's "
        "website?"
    )


async def _create_gateway_session(body: CreateSessionRequest) -> dict:
    """Gateway mode: register a per-interview Custom LLM (secret + LLM config
    pointing back at our /llm/{interview_id}/v1 endpoint) plus a minimal
    context, then create the session token with no Gemini fallback."""
    try:
        liveavatar_key = resolve_api_key()

        state = interview_state.get(body.interview_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Unknown interview")

        secret_id = await create_llm_secret(liveavatar_key, state.gateway_token)
        gateway_base_url = f"{settings.public_base_url.rstrip('/')}/llm/{body.interview_id}/v1"
        llm_config_id = await create_llm_configuration(liveavatar_key, secret_id, gateway_base_url)
        context_id = await create_context(liveavatar_key, GATEWAY_CONTEXT_PROMPT, _gateway_opening_text())

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
    # Gateway-only: every session is created against a pre-existing interview
    # (registered via /api/interview) and requires this backend to be
    # externally reachable so HeyGen can call back into /llm/{id}/v1.
    if not body.interview_id:
        raise HTTPException(status_code=400, detail="interview_id is required")
    if not settings.public_base_url:
        raise HTTPException(status_code=503, detail="PUBLIC_BASE_URL is not configured")

    return await _create_gateway_session(body)


@router.post("/api/session/stop")
async def stop_session_route(body: StopSessionRequest):
    try:
        if not body.session_token:
            return {"status": "ignored"}

        liveavatar_key = resolve_api_key()

        res = await stop_session(body.session_token)

        active_sessions.decrement()

        # Clean up the dynamically created context if one was used
        if body.context_id:
            try:
                await delete_context(liveavatar_key, body.context_id)
            except Exception as e:
                logger.warning("Failed to clean up context %s: %s", body.context_id, e)

        # Gateway mode: best-effort teardown of the per-interview LLM config,
        # secret and context. Each failure is logged, never propagated.
        state = interview_state.get(body.interview_id) if body.interview_id else None
        if state is not None:
            if state.llm_config_id:
                try:
                    await delete_llm_configuration(liveavatar_key, state.llm_config_id)
                except Exception as e:
                    logger.warning("Failed to clean up LLM config %s: %s", state.llm_config_id, e)
            if state.secret_id:
                try:
                    await delete_secret(liveavatar_key, state.secret_id)
                except Exception as e:
                    logger.warning("Failed to clean up secret %s: %s", state.secret_id, e)
            if state.context_id:
                try:
                    await delete_context(liveavatar_key, state.context_id)
                except Exception as e:
                    logger.warning("Failed to clean up context %s: %s", state.context_id, e)
            state.status = "finished"

        return {"status": "stopped", "api_status": res.status_code}
    except Exception as e:
        logger.error("Error stopping session: %s", str(e))
        raise HTTPException(status_code=500, detail="Failed to stop session")
