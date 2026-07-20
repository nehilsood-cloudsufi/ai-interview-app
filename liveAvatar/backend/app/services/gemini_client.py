"""Shared Gemini chat call for all agents (host, appraiser, coordinator,
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

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _is_model_error(response: httpx.Response) -> bool:
    if response.status_code == 404:
        return True
    return response.status_code == 400 and "model" in response.text.lower()


async def chat_completion(payload: dict, *, timeout: float, fallback_model: str | None = None) -> dict:
    """POST one chat-completions request and return the parsed response JSON.

    Raises on any HTTP failure (via raise_for_status), except a model-not-
    found error, which triggers a single retry with `fallback_model` (a 404
    returns in well under a second, so even the Host's tight turn budget
    absorbs the retry)."""
    headers = {
        "Authorization": f"Bearer {settings.gemini_api_key}",
        "Content-Type": "application/json",
    }
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
