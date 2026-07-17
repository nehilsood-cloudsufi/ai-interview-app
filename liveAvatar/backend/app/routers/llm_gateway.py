"""OpenAI-compatible LLM gateway for HeyGen's Custom LLM callback.

HeyGen's LiveKit agent POSTs a standard chat-completions request here once
per user utterance (observed contract in docs/llm-gateway-notes.md; response
shapes proven live by the Phase 0 spike this route replaces). Auth failures
may return real 4xx; after auth the route never fails - any error becomes an
HTTP 200 with a canned reply so the avatar always has something to say.
"""

import asyncio
import json
import logging
import secrets
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.config import settings
from app.services import host_agent, interview_state
from app.services.interview_config import QuestionNode, get_questionnaire, get_rubric
from app.services.interview_state import InterviewState

logger = logging.getLogger(__name__)

router = APIRouter()

# Spoken when HeyGen probes the endpoint before any user utterance exists.
_GREETING_REPLY = "Hello! Thanks for joining. Whenever you're ready, we can begin."


async def _on_answer_complete(state: InterviewState, question: QuestionNode, answer_text: str) -> None:
    """Background hook fired each time the vendor completes an answer.

    Placeholder: Task C2 replaces the body with the Appraiser call."""


def _fire_answer_complete_hook(state: InterviewState, question: QuestionNode, answer_text: str) -> None:
    async def _run() -> None:
        try:
            await _on_answer_complete(state, question, answer_text)
        except Exception:
            logger.warning(
                "answer-complete hook failed for interview %s (question %s).",
                state.interview_id,
                question.id,
                exc_info=True,
            )

    try:
        asyncio.create_task(_run())
    except Exception:
        logger.warning("Could not schedule answer-complete hook for interview %s.", state.interview_id, exc_info=True)


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


def _stream_response(completion_id: str, created: int, model: str, reply: str) -> StreamingResponse:
    def chunk(delta: dict, finish_reason: str | None) -> str:
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(payload)}\n\n"

    async def sse():
        yield chunk({"content": reply}, None)
        yield chunk({}, "stop")
        yield "data: [DONE]\n\n"

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
    stream = True  # safe default when the body itself is unparsable (HeyGen always streams)
    model = "resonance-host"
    try:
        body = await request.json()
        stream = bool(body.get("stream"))
        model = body.get("model") or model
        user_text = _last_user_text(body)
        if user_text is None:
            reply = _GREETING_REPLY
        else:
            result = await host_agent.handle_turn(state, user_text, get_questionnaire(), get_rubric())
            reply = result.reply
            if result.answer_complete:
                _fire_answer_complete_hook(state, result.completed_question, result.answer_text)
    except Exception:
        logger.warning("LLM gateway turn failed for interview %s; returning canned reply.", interview_id, exc_info=True)
        reply = settings.host_fallback_reply

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    created = int(time.time())
    if stream:
        return _stream_response(completion_id, created, model, reply)
    return _completion_response(completion_id, created, model, reply)
