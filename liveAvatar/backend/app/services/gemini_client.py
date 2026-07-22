"""Shared Gemini chat call for all agents (host, evaluator, coordinator,
summary): one POST to the OpenAI-compatible endpoint with the standard auth
headers, plus the model-fallback rule.

The configured models are Gemini's auto-tracking "-latest" aliases
(`gemini-flash-latest` / `gemini-pro-latest`), which Google hot-swaps with
only two weeks' email notice. If a call fails because the *model* is unknown
or unavailable (404, or a 400 whose body mentions the model), the request is
retried exactly once with the caller's pinned fallback model so an alias
change can never take the app down. Every other failure raises unchanged -
callers keep their own soft-fail/retry policies on top.
"""

import json
import logging
from collections.abc import AsyncIterator

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _auth_headers() -> dict[str, str]:
    """Bearer-auth + JSON content-type headers for the OpenAI-compatible endpoint."""
    return {
        "Authorization": f"Bearer {settings.gemini_api_key}",
        "Content-Type": "application/json",
    }


def _is_model_error(response: httpx.Response) -> bool:
    """Whether a failed buffered response looks like a model-not-found error -
    a 404, or a 400 whose body mentions the model - and so warrants the
    single fallback-model retry rather than propagating."""
    if response.status_code == 404:
        return True
    return response.status_code == 400 and "model" in response.text.lower()


async def chat_completion(payload: dict, *, timeout: float, fallback_model: str | None = None) -> dict:
    """POST one chat-completions request and return the parsed response JSON.

    Raises on any HTTP failure (via raise_for_status), except a model-not-
    found error, which triggers a single retry with `fallback_model` (a 404
    returns in well under a second, so even the Host's tight turn budget
    absorbs the retry)."""
    headers = _auth_headers()
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{settings.gemini_base_url}chat/completions", json=payload, headers=headers)
        if (
            fallback_model
            and fallback_model != payload.get("model")
            and _is_model_error(response)
        ):
            logger.warning(
                "Gemini model %r unavailable (HTTP %d); retrying once with fallback model %r.",
                payload.get("model"),
                response.status_code,
                fallback_model,
            )
            response = await client.post(
                f"{settings.gemini_base_url}chat/completions",
                json={**payload, "model": fallback_model},
                headers=headers,
            )
        response.raise_for_status()
        return response.json()


async def _is_model_error_stream(response: httpx.Response) -> bool:
    """Streaming counterpart to `_is_model_error`. Because a streamed response's
    body has not been read yet, this awaits `response.aread()` before doing the
    400-body "model" check; a 404 short-circuits without reading."""
    # A streamed response's body isn't read yet; the 400 text check needs it.
    if response.status_code == 404:
        return True
    if response.status_code == 400:
        await response.aread()
        return "model" in response.text.lower()
    return False


async def _iter_deltas(response: httpx.Response) -> AsyncIterator[str]:
    """Yield `choices[0].delta.content` fragments from an SSE chat stream,
    stopping at the `[DONE]` sentinel and ignoring blank/keepalive lines."""
    async for line in response.aiter_lines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            return
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        delta = (obj.get("choices") or [{}])[0].get("delta", {}).get("content")
        if delta:
            yield delta


async def stream_chat_completion(
    payload: dict, *, timeout: float, fallback_model: str | None = None
) -> AsyncIterator[str]:
    """Stream a chat-completions call, yielding content deltas as they arrive.

    Sets `stream: true`. The model-not-found fallback is resolved on the
    response status *before* any delta is yielded (safe to retry while nothing
    has been spoken yet); every other HTTP error raises before the first yield.
    """
    headers = _auth_headers()
    url = f"{settings.gemini_base_url}chat/completions"
    body = {**payload, "stream": True}
    async with httpx.AsyncClient(timeout=timeout) as client:
        retry = False
        async with client.stream("POST", url, json=body, headers=headers) as response:
            if (
                fallback_model
                and fallback_model != payload.get("model")
                and await _is_model_error_stream(response)
            ):
                retry = True
            else:
                response.raise_for_status()
                async for delta in _iter_deltas(response):
                    yield delta
        if not retry:
            return
        logger.warning(
            "Gemini model %r unavailable (HTTP fallback); retrying stream once with %r.",
            payload.get("model"),
            fallback_model,
        )
        async with client.stream(
            "POST", url, json={**body, "model": fallback_model}, headers=headers
        ) as response:
            response.raise_for_status()
            async for delta in _iter_deltas(response):
                yield delta
