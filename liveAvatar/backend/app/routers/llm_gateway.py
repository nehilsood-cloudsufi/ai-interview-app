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
from app.services.interview_config import get_questionnaire, get_rubric

logger = logging.getLogger(__name__)

router = APIRouter()

# Spoken when HeyGen probes the endpoint before any user utterance exists.
_GREETING_REPLY = "Hello! Thanks for joining. Whenever you're ready, we can begin."


def _last_user_text(body: dict) -> str | None:
    """Pull the vendor's latest utterance out of an OpenAI chat-completions
    request body, or None if the request carries no user message yet (the
    pre-utterance greeting probe).

    The latest utterance is the TRAILING RUN of consecutive `user` messages
    joined in order - not just the final message. HeyGen's VAD splits flowing
    speech into fragments and cancels the in-flight completion each time a
    new fragment arrives; the cancelled reply never enters HeyGen's history,
    so the next request shows the earlier fragments as back-to-back user
    messages (verified live 2026-07-22 via the tunnel inspector: 'I' / 'work
    as an engineer at CloudSufi.' / 'And I'm working there for about 6
    months.' arrived as three consecutive user messages). Joining the run
    reassembles the vendor's full utterance for the Host to judge."""
    messages = body.get("messages") or []

    run: list[str] = []
    for message in reversed(messages):
        if message.get("role") == "user":
            content = str(message.get("content") or "")
            if content:
                run.append(content)
        else:
            break
    if run:
        return " ".join(reversed(run))

    # No trailing user run (e.g. the history ends with an assistant turn):
    # fall back to the newest user message anywhere, the original behavior.
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return None


def _completion_response(completion_id: str, created: int, model: str, reply: str) -> dict:
    """Build the non-streaming OpenAI `chat.completion` response dict HeyGen
    expects, wrapping `reply` as the single assistant choice with a "stop"
    finish reason. Used when HeyGen did not request a streamed response."""
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
    """Format one Server-Sent-Events line carrying a
    `chat.completion.chunk`: the `delta` is the incremental piece (e.g.
    `{"content": "..."}` for a reply fragment, `{}` with
    finish_reason="stop" to close the stream). Returns the fully-framed
    `data: <json>\\n\\n` string ready to yield to HeyGen."""
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def _stream_response(completion_id: str, created: int, model: str, reply: str) -> StreamingResponse:
    """Wrap an already-decided `reply` as a minimal SSE stream: one content
    chunk, a stop chunk, then `[DONE]`. This is the streamed-but-not-
    token-by-token path - it emits the whole reply in a single chunk, used
    for the greeting, for canned fallbacks, and whenever token-level
    streaming from the Host isn't in play but HeyGen still asked to stream."""
    async def sse():
        yield _chunk(completion_id, created, model, {"content": reply}, None)
        yield _chunk(completion_id, created, model, {}, "stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


def _streaming_host_response(
    state,
    interview_id: str,
    user_text: str,
    completion_id: str,
    created: int,
    model: str,
    started: float,
    is_cancelled=None,
) -> StreamingResponse:
    """True token-by-token path: forward the Host's reply fragments to HeyGen as
    Gemini produces them. Never-fail: a failure before the first fragment is
    spoken falls back to the canned reply; a failure after leaves the partial
    reply standing (the avatar is already talking). `is_cancelled` is the
    gateway's seq-based supersede signal - stream_turn skips or discards a
    superseded turn without touching state."""

    async def sse():
        outcome = host_agent.StreamedTurn()
        spoke = False
        ttft_ms: int | None = None
        try:
            async for text in host_agent.stream_turn(
                state, user_text, get_questionnaire(state.domain), get_rubric(), outcome, is_cancelled=is_cancelled
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
    """OpenAI-compatible chat-completions endpoint HeyGen's LiveKit agent
    calls once per vendor utterance. The `interview_id` path parameter names
    the interview, and the request must carry `Authorization: Bearer
    <gateway_token>` matching that interview's token. The body is a standard
    chat-completions payload (`messages`, `model`, `stream`); only the latest
    user message is used - the running interview state lives on our side, not
    in the replayed history.

    Responds in the OpenAI `chat.completion` shape (streamed as SSE when the
    request set `stream: true`, which HeyGen always does; buffered otherwise).
    Before any user utterance exists it speaks a canned greeting. This is NOT
    a general consumer endpoint - it is HeyGen's private callback, so it
    fails with 404 for an unknown `interview_id` and 401 for a missing or
    wrong bearer token, but past auth it never fails: any downstream error
    (including an unparsable body) becomes a 200 with a canned reply, because
    HeyGen does not retry and the avatar must always have something to say.
    """
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

    if user_text is not None:
        # Supersede detection is OUR bookkeeping, not the socket's: bump the
        # interview's request counter and remember this request's seq. HeyGen
        # cancels a request the moment a newer speech fragment arrives, but
        # request.is_disconnected proved unreliable through the tunnel/uvicorn
        # stack (verified live 2026-07-22 - zero cancellations detected while
        # four questions were silently consumed). The seq comparison always
        # works: a newer fragment bumps the counter, so this turn knows it's
        # been superseded. is_disconnected stays as a free extra signal.
        state.request_seq += 1
        seq = state.request_seq

        async def superseded() -> bool:
            return state.request_seq != seq or await request.is_disconnected()

        # The settle beat: a human interviewer waits a moment before
        # answering. If the vendor was only pausing mid-thought, the next
        # fragment lands during this sleep and this turn steps aside.
        if settings.host_utterance_settle_seconds > 0:
            await asyncio.sleep(settings.host_utterance_settle_seconds)
    else:
        superseded = None

    # Opt-in token-by-token path: only for a real user utterance HeyGen wants
    # streamed. Greeting/non-stream turns keep the buffered path below.
    if settings.host_streaming_enabled and stream and user_text is not None:
        return _streaming_host_response(
            state, interview_id, user_text, completion_id, created, model, started, superseded
        )

    answer_complete = False
    try:
        if user_text is None:
            reply = _GREETING_REPLY
        else:
            # handle_turn re-checks under the turn lock and again after the
            # Gemini call; a None result means the turn was superseded and
            # state untouched - answer with a stub, nobody is reading it.
            result = await host_agent.handle_turn(
                state,
                user_text,
                get_questionnaire(state.domain),
                get_rubric(),
                is_cancelled=superseded,
            )
            if result is None:
                logger.info(
                    "Gateway turn superseded: interview=%s node=%s seq=%d head=%d elapsed_ms=%d",
                    interview_id,
                    state.current_node_id,
                    seq,
                    state.request_seq,
                    int((time.monotonic() - started) * 1000),
                )
                return _completion_response(completion_id, created, model, "")
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
