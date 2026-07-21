"""DATA SCOUT — post-interview internet research on the vendor company; never
raises into the pipeline; knows nothing about the interview flow or rubric.

Runs once, after an interview ends. `GeminiSearchProvider` is the only
implementation today: a single call to Gemini's NATIVE REST API (not the
OpenAI-compatible endpoint gemini_client.py wraps - that endpoint does not
support the Google Search grounding tool) with `tools: [{"google_search": {}}]`
enabled. `run()` is the whole soft-fail boundary: any HTTP error, timeout, or
parse failure anywhere in the provider is swallowed and logged, and an empty
findings list is returned - a research hiccup must never break the
Scout -> Evaluator -> Coordinator pipeline.
"""

import logging
from typing import Protocol

import httpx

from app.config import settings
from app.services.interview_state import InterviewState, ScoutFinding
from app.services.llm_json import parse_llm_json

logger = logging.getLogger(__name__)

# Background pipeline step - nobody is waiting on this turn-by-turn the way
# the Host is on its per-turn budget, so a generous timeout beats a
# premature failure.
_TIMEOUT = 45.0


class ResearchProvider(Protocol):
    """The entire provider seam: a different backend (e.g. Tavily) can be
    added later by implementing this one method. No registry, no
    config-driven selection, no factory."""

    async def research(self, company_name: str, website: str | None) -> list[ScoutFinding]: ...


def _is_model_error(response: httpx.Response) -> bool:
    # Mirrors gemini_client._is_model_error. Duplicated locally (not
    # imported) because that helper belongs to the OpenAI-compatible
    # transport this module deliberately does not use.
    if response.status_code == 404:
        return True
    return response.status_code == 400 and "model" in response.text.lower()


def _grounding_urls(data: dict) -> list[str]:
    """Best-effort extraction of grounding source URIs. Wrapped defensively:
    groundingMetadata's shape is not a stable contract, and a shape change
    here must never break the Scout."""
    try:
        candidate = data["candidates"][0]
        chunks = candidate.get("groundingMetadata", {}).get("groundingChunks") or []
        return [chunk["web"]["uri"] for chunk in chunks if chunk.get("web", {}).get("uri")]
    except Exception:
        logger.warning("Could not read groundingMetadata from Gemini response.", exc_info=True)
        return []


def _coerce_findings(raw: object, grounding_urls: list[str]) -> list[ScoutFinding]:
    """Defensively turn the LLM's proposed findings into ScoutFindings:
    skip entries missing topic/summary, and backfill a missing source_url
    from the first not-yet-used grounding URI."""
    if not isinstance(raw, list):
        return []
    used: set[str] = set()
    findings: list[ScoutFinding] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        topic = entry.get("topic")
        summary = entry.get("summary")
        if not topic or not summary:
            continue
        source_url = entry.get("source_url") or None
        if not source_url:
            source_url = next((url for url in grounding_urls if url not in used), None)
        if source_url:
            used.add(source_url)
        findings.append(ScoutFinding(topic=str(topic), summary=str(summary), source_url=source_url))
    return findings


class GeminiSearchProvider:
    """Researches a vendor company via Gemini's native generateContent API
    with the google_search grounding tool enabled. Structured output
    (responseMimeType/responseSchema) cannot be combined with that tool, so
    the JSON contract is requested in the prompt and parsed defensively."""

    async def research(self, company_name: str, website: str | None) -> list[ScoutFinding]:
        user_text = (
            f"Vendor company name: {company_name}\n"
            f"Vendor website: {website or 'unknown'}\n\n"
            f"{settings.scout_research_prompt}"
        )
        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "tools": [{"google_search": {}}],
        }
        headers = {"x-goog-api-key": settings.gemini_api_key or "", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            url = f"{settings.gemini_native_base_url}models/{settings.gemini_model}:generateContent"
            response = await client.post(url, json=payload, headers=headers)
            if settings.gemini_model_fallback != settings.gemini_model and _is_model_error(response):
                logger.warning(
                    "Gemini model %r unavailable (HTTP %d); retrying Scout research once with fallback %r.",
                    settings.gemini_model,
                    response.status_code,
                    settings.gemini_model_fallback,
                )
                fallback_url = (
                    f"{settings.gemini_native_base_url}models/{settings.gemini_model_fallback}:generateContent"
                )
                response = await client.post(fallback_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        text = "".join(part.get("text", "") for part in data["candidates"][0]["content"]["parts"])
        # The prompt asks for a JSON object (not a bare array) so the shared
        # parse_llm_json helper can be reused as-is: it only ever extracts a
        # top-level JSON *object* (see tests/services/test_llm_json.py -
        # top-level arrays are rejected there by design), and a bare array of
        # objects would silently decode to just its first element.
        parsed = parse_llm_json(text)
        return _coerce_findings(parsed.get("findings"), _grounding_urls(data))


async def run(state: InterviewState, provider: ResearchProvider | None = None) -> list[ScoutFinding]:
    """Run the Data Scout for one finished interview.

    NEVER raises: any HTTP error, timeout, or parse failure anywhere in the
    provider is logged and treated as "no findings" - a research hiccup must
    never break the pipeline that follows."""
    if not settings.scout_enabled:
        logger.info("Data Scout disabled (scout_enabled=False); skipping interview %s.", state.interview_id)
        return []

    company_name = state.vendor_profile.company_name.strip()
    if not company_name:
        logger.info("No company name captured for interview %s; skipping Scout research.", state.interview_id)
        return []

    active_provider = provider or GeminiSearchProvider()
    try:
        findings = await active_provider.research(company_name, state.vendor_profile.website)
    except Exception:
        logger.warning(
            "Data Scout research failed for interview %s; continuing with no findings.",
            state.interview_id,
            exc_info=True,
        )
        return []

    state.scout_findings = findings
    return findings
