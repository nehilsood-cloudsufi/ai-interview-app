"""OpenAI-compatible LLM gateway for HeyGen's Custom LLM callback.

HeyGen's LiveKit agent POSTs a standard chat-completions request here once
per user utterance (observed contract in docs/llm-gateway-notes.md; response
shapes proven live by the Phase 0 spike this route replaces). Auth failures
may return real 4xx; after auth the route never fails - any error becomes an
HTTP 200 with a canned reply so the avatar always has something to say.
"""

import json
import logging
import secrets
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.config import settings
from app.services import host_agent, interview_state
from app.services.interview_config import get_questionnaire, get_rubric

logger = logging.getLogger(__name__)

router = APIRouter()

# Spoken when HeyGen probes the endpoint before any user utterance exists.
_GREETING_REPLY = "Hello! Thanks for joining. Whenever you're ready, we can begin."


def _last_user_text(body: dict) -> str | None:
    # Full history is resent every turn; the latest user utterance is always
    # the final "user" message (spike finding).
    for message in reversed(body.get("messages") or []):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return None


def _completion_response(completion_id: str, created: int, model: str, reply: str) -> dict:
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": reply},
                "finish_reason": "stop",
            }
        ],
    }


def _chunk(completion_id: str, created: int, model: str, delta: dict, finish_reason: str | None) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def _stream_response(completion_id: str, created: int, model: str, reply: str) -> StreamingResponse:
    async def sse():
        yield _chunk(completion_id, created, model, {"content": reply}, None)
        yield _chunk(completion_id, created, model, {}, "stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


def _streaming_host_response(
    state, interview_id: str, user_text: str, completion_id: str, created: int, model: str, started: float
) -> StreamingResponse:
    """True token-by-token path: forward the Host's reply fragments to HeyGen as
    Gemini produces them. Never-fail: a failure before the first fragment is
    spoken falls back to the canned reply; a failure after leaves the partial
    reply standing (the avatar is already talking)."""

    async def sse():
        outcome = host_agent.StreamedTurn()
        spoke = False
        ttft_ms: int | None = None
        try:
            async for text in host_agent.stream_turn(
                state, user_text, get_questionnaire(), get_rubric(), outcome
            ):
                if not text:
                    continue
                if not spoke:
                    ttft_ms = int((time.monotonic() - started) * 1000)
                    spoke = True
                yield _chunk(completion_id, created, model, {"content": text}, None)
        except Exception:
            logger.warning(
                "LLM gateway streaming turn failed for interview %s; returning canned reply.",
                interview_id,
                exc_info=True,
            )
            if not spoke:
                yield _chunk(completion_id, created, model, {"content": settings.host_fallback_reply}, None)
        yield _chunk(completion_id, created, model, {}, "stop")
        yield "data: [DONE]\n\n"

        answer_complete = outcome.result.answer_complete if outcome.result else False
        # ttft_ms is the metric this path exists to improve: server time until
        # the avatar's first spoken word, vs elapsed_ms for the whole turn.
        logger.info(
            "Gateway turn (stream): interview=%s node=%s answer_complete=%s ttft_ms=%s elapsed_ms=%d",
            interview_id,
            state.current_node_id,
            answer_complete,
            ttft_ms if ttft_ms is not None else "n/a",
            int((time.monotonic() - started) * 1000),
        )

    return StreamingResponse(sse(), media_type="text/event-stream")


@router.post("/llm/{interview_id}/v1/chat/completions")
async def chat_completions(interview_id: str, request: Request):
    state = interview_state.get(interview_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown interview")

    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, state.gateway_token):
        raise HTTPException(status_code=401, detail="Invalid gateway token")

    # Never-fail zone: HeyGen does not retry and the avatar must always have
    # something to say, so any failure below becomes the canned reply.
    started = time.monotonic()
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    created = int(time.time())

    # An unparsable body can't tell us the requested format, so default to a
    # streamed canned reply (HeyGen always streams).
    try:
        body = await request.json()
        stream = bool(body.get("stream"))
        model = body.get("model") or "resonance-host"
        user_text = _last_user_text(body)
    except Exception:
        logger.warning("LLM gateway body unparsable for interview %s; returning canned reply.", interview_id, exc_info=True)
        return _stream_response(completion_id, created, "resonance-host", settings.host_fallback_reply)

    # Opt-in token-by-token path: only for a real user utterance HeyGen wants
    # streamed. Greeting/non-stream turns keep the buffered path below.
    if settings.host_streaming_enabled and stream and user_text is not None:
        return _streaming_host_response(state, interview_id, user_text, completion_id, created, model, started)

    answer_complete = False
    try:
        if user_text is None:
            reply = _GREETING_REPLY
        else:
            result = await host_agent.handle_turn(state, user_text, get_questionnaire(), get_rubric())
            reply = result.reply
            # answer_complete only feeds the timing log now - scoring happens
            # once, holistically, at finalize (never mid-interview).
            answer_complete = result.answer_complete
    except Exception:
        logger.warning("LLM gateway turn failed for interview %s; returning canned reply.", interview_id, exc_info=True)
        reply = settings.host_fallback_reply

    # Per-turn latency evidence: this measures only our server time; the rest
    # of the perceived turn-around (HeyGen STT, tunnel, TTS) lives outside it.
    logger.info(
        "Gateway turn: interview=%s node=%s answer_complete=%s elapsed_ms=%d",
        interview_id,
        state.current_node_id,
        answer_complete,
        int((time.monotonic() - started) * 1000),
    )

    if stream:
        return _stream_response(completion_id, created, model, reply)
    return _completion_response(completion_id, created, model, reply)
