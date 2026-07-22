"""LiveAvatar session lifecycle endpoints (gateway mode only).

A "session" is one live HeyGen avatar stream. Creating one is a multi-step
LiveAvatar provisioning dance done server-side per interview: register a
per-interview Custom LLM (a secret holding the interview's gateway_token,
plus an LLM configuration pointing HeyGen back at our
`/llm/{interview_id}/v1` endpoint) and a minimal context, then mint a
session token the browser SDK uses to open the stream. Because HeyGen must
be able to call back into our gateway, this only works when the interview
already exists in memory (created via POST /api/interview) and
`PUBLIC_BASE_URL` is set to a URL HeyGen can reach. There is no legacy
non-gateway mode.

The avatar/credits used depend on the interview's tier (chosen at interview
creation): dev tier uses the free sandbox avatar with is_sandbox=True (~1-min
HeyGen cap), prod tier uses PROD_AVATAR_ID with is_sandbox=False and a
credit-bounded max_session_duration. Stopping tears the LiveAvatar resources
back down best-effort. Same-origin UI endpoints - no auth.
"""

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
    greeting. Content is aligned with `intro`'s ask: name, role, and
    company."""
    return (
        "Hello, and welcome! I'm Noor, and I'll be running today's vendor "
        "evaluation. To get us started, could you introduce yourself - your "
        "name, your role, and the company you represent?"
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

        if state.tier == "prod":
            # Re-checked here (not just at interview creation) in case the
            # config changed in between; a prod-tier session must never fall
            # back to the sandbox avatar silently.
            if not settings.prod_avatar_id:
                raise HTTPException(
                    status_code=503, detail="Production tier is not configured (PROD_AVATAR_ID required)"
                )
            avatar_id = settings.prod_avatar_id
            is_sandbox = False
            voice_id = settings.prod_voice_id
            # Picked on the start screen at interview creation; the settings
            # ceiling only backstops states created before that field existed.
            # The grace buffer moves HeyGen's hard cut past the Host's own
            # time-aware wrap-up (host_agent), so the closing is spoken while
            # the stream is still alive and the cap is purely a safety net.
            max_session_duration = (
                state.max_session_seconds or settings.prod_max_session_seconds
            ) + settings.prod_session_grace_seconds
        else:
            avatar_id = settings.avatar_id
            is_sandbox = True
            voice_id = None
            max_session_duration = None

        secret_id = await create_llm_secret(liveavatar_key, state.gateway_token)
        gateway_base_url = f"{settings.public_base_url.rstrip('/')}/llm/{body.interview_id}/v1"
        llm_config_id = await create_llm_configuration(liveavatar_key, secret_id, gateway_base_url)
        context_id = await create_context(liveavatar_key, GATEWAY_CONTEXT_PROMPT, _gateway_opening_text())

        token_data = await create_session_token(
            liveavatar_key,
            llm_config_id,
            context_id,
            avatar_id=avatar_id,
            is_sandbox=is_sandbox,
            voice_id=voice_id,
            max_session_duration=max_session_duration,
        )

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
    """Provision and open a LiveAvatar session for an existing interview.

    The request body (`CreateSessionRequest`) must carry the `interview_id`
    of an interview previously created via POST /api/interview; `avatar_id`
    in the body is currently ignored (the avatar is chosen server-side from
    the interview's tier). On success responds with a JSON object holding
    `session_token` (the token the browser SDK uses to connect the stream)
    and `session_id` (HeyGen's id for the stream), and stores the created
    LiveAvatar resource ids on the interview state for later teardown.

    Fails with 400 if `interview_id` is missing, and 503 if
    `PUBLIC_BASE_URL` is not configured (HeyGen needs a public callback URL).
    Beyond those, the underlying provisioning can return 404 if the
    interview id is unknown, 503 if the interview is prod-tier but
    PROD_AVATAR_ID is unset, the upstream LiveAvatar status code if one of
    its API calls fails, or 500 for any other unexpected error.
    """
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
    """Stop a running LiveAvatar session and tear down its resources.

    The request body (`StopSessionRequest`) carries the `session_token` to
    stop, plus optionally `context_id` and `interview_id` so the
    per-interview LLM configuration, secret, and context provisioned at
    creation can be cleaned up. If no `session_token` is provided this is a
    no-op that responds `{"status": "ignored"}`. Otherwise it asks
    LiveAvatar to stop the stream, decrements the active-session counter,
    best-effort deletes the associated LiveAvatar resources (each failure is
    logged, never surfaced), marks the interview state "finished", and
    responds `{"status": "stopped", "api_status": <upstream status code>}`.
    Returns 500 only if stopping the session itself raises.

    Note this is the only path that decrements the concurrency counter -
    sessions HeyGen ends on its own (hitting the duration cap) never call
    here, so the counter can drift (see the module note in
    app.services.session_state).
    """
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
