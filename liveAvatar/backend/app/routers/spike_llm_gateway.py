"""SPIKE — Phase 0 of the Resonance multi-agent plan.

Throwaway endpoint that lets us observe exactly how HeyGen calls a custom
LLM: full request shape, whether history/system prompt are included,
streaming vs non-streaming, and how our secret arrives in the Authorization
header. Delete this file (and its one line in app/main.py) once
docs/llm-gateway-notes.md is written and Phase 1 replaces it with the real
gateway route (app/routers/llm_gateway.py, Task B2).
"""

import json
import logging
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_SPIKE_REPLY = (
    "Hello! This reply came from our own backend gateway spike endpoint, "
    "not from HeyGen's own AI. If you can hear this, the custom LLM wiring works."
)


@router.post("/llm/spike/v1/chat/completions")
async def spike_chat_completions(request: Request):
    body = await request.json()
    logger.info("SPIKE headers: %s", dict(request.headers))
    logger.info("SPIKE body: %s", json.dumps(body, indent=2))

    completion_id = f"chatcmpl-spike-{uuid.uuid4().hex[:8]}"
    created = int(time.time())
    model = body.get("model", "resonance-host")

    if not body.get("stream"):
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": _SPIKE_REPLY},
                    "finish_reason": "stop",
                }
            ],
        }

    async def sse():
        for word in _SPIKE_REPLY.split(" "):
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": word + " "}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
        final_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(final_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")
