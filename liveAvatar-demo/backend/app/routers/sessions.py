import logging

import httpx
from fastapi import APIRouter, HTTPException

from app.dependencies import resolve_api_key
from app.models import CreateSessionRequest, StopSessionRequest
from app.services import gemini_provisioning
from app.services.liveavatar_client import create_session_token, delete_context, stop_session
from app.services.session_state import active_sessions

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/session")
async def create_session(body: CreateSessionRequest):
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

        return {"status": "stopped", "api_status": res.status_code}
    except Exception as e:
        logger.error("Error stopping session: %s", str(e))
        raise HTTPException(status_code=500, detail="Failed to stop session")
