"""Data Scout Agent public entry point: orchestrates gathering
(scout_gather) then synthesizes the final report via Gemini. run_scout is
the only function other modules (app.services.pipeline) call.
"""

import asyncio
import json
import logging
from typing import Any

import httpx

from app.config import settings
from app.services import scout_gather, scout_utils

logger = logging.getLogger(__name__)

# --- Gemini call tuning ---
# Gemini occasionally returns 5xx (server-side overload) on large/complex
# requests like the synthesis call - these are transient, unlike 4xx (bad
# request/auth/quota), which retrying can't fix. A couple of short-backoff
# retries clears most of them without meaningfully slowing down the common
# case. Claim extraction has no retry loop: it soft-fails to [] on any
# error (see scout_gather._extract_claims), so a single attempt is enough.
_SYNTHESIS_MAX_ATTEMPTS = 3
_SYNTHESIS_RETRY_BACKOFF_SECONDS = 3.0
_SYNTHESIS_TIMEOUT_SECONDS = 180.0  # per-attempt httpx timeout; large payloads take a while to process

_GATHER_TIMEOUT_SECONDS = 45.0  # total wall-clock budget for Pass A + Pass B combined before synthesis proceeds with partial data


async def _post_to_gemini(payload: dict[str, Any]) -> str:
    """Sends a chat/completions payload to Gemini with retry-on-5xx/timeout
    (up to _SYNTHESIS_MAX_ATTEMPTS), raising immediately on 4xx. Returns the
    model's reply text; raises the last error if all attempts fail."""
    last_error: Exception | None = None
    for attempt in range(1, _SYNTHESIS_MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=_SYNTHESIS_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    scout_gather._gemini_chat_completions_url(),
                    json=payload,
                    headers=scout_gather._gemini_headers(),
                )
                if response.status_code != 200:
                    logger.error(
                        "Gemini synthesis HTTP %s on attempt %d/%d: %s",
                        response.status_code, attempt, _SYNTHESIS_MAX_ATTEMPTS,
                        response.text[:500],
                    )
                response.raise_for_status()
                response_body = response.json()
            return response_body["choices"][0]["message"]["content"].strip()
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if exc.response.status_code < 500 or attempt == _SYNTHESIS_MAX_ATTEMPTS:
                raise
            logger.warning(
                "Gemini synthesis attempt %d/%d got %s (%s), retrying in %.0fs",
                attempt, _SYNTHESIS_MAX_ATTEMPTS, exc.response.status_code, exc, _SYNTHESIS_RETRY_BACKOFF_SECONDS,
            )
            await asyncio.sleep(_SYNTHESIS_RETRY_BACKOFF_SECONDS)
        except httpx.ReadTimeout as exc:
            last_error = exc
            if attempt == _SYNTHESIS_MAX_ATTEMPTS:
                raise
            logger.warning(
                "Gemini synthesis attempt %d/%d timed out (%s), retrying in %.0fs",
                attempt, _SYNTHESIS_MAX_ATTEMPTS, exc, _SYNTHESIS_RETRY_BACKOFF_SECONDS,
            )
            await asyncio.sleep(_SYNTHESIS_RETRY_BACKOFF_SECONDS)

    raise last_error  # type: ignore[misc]


async def _synthesize_internet_findings(company_name: str, company_website: str | None, sources: dict[str, Any]) -> str:
    """Truncates the sources payload, then calls Gemini to synthesize the
    full two-section report (Interview Claims, Additional Findings - see
    scout_system_prompt). Returns the rendered markdown; raises on any
    unrecoverable failure so the caller can soft-fail while preserving the
    already-gathered sources.

    Unlike the prior peer-sections design, this DOES receive
    interview_claims - the claim-focused report format requires mapping each
    claim to what public sources say about it, in neutral (non-verification)
    language; see scout_system_prompt's banned-language list for how that
    stays Scout-appropriate rather than Evaluator-appropriate."""
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured; cannot synthesize scout findings.")

    truncated_sources = scout_utils._truncate_sources(sources)
    payload_json = json.dumps(truncated_sources, ensure_ascii=False, indent=2)
    logger.info("Synthesis payload size: sources JSON=%d chars", len(payload_json))

    user_content = (
        f"Company name: {company_name}\n"
        f"Company website: {company_website or 'not provided'}\n\n"
        "Structured data gathered from public web sources and the interview "
        "transcript (omitted keys mean that source wasn't found or wasn't "
        "applicable):\n"
        f"{payload_json}"
    )
    payload = {
        "model": settings.scout_gemini_model,
        "messages": [
            {"role": "system", "content": settings.scout_system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    return await _post_to_gemini(payload)


async def run_scout(
    company_name: str,
    representative_name: str,
    extra_context: str | None,
    documents: list[str] | None,
    transcript: str | None,
) -> dict[str, Any]:
    """Orchestrates the two-pass Data Scout Agent pipeline. Returns
    {"internet_findings": str, "interview_claims": list[str],
    "sources": dict, "findings_ok": bool, "company_website": str | None}.

    There is no dedicated website field on the intake form anymore - Pass A
    derives it itself (see scout_gather._derive_company_website) from any URL
    mentioned in extra_context/documents, falling back to a Tavily search for
    the company's official website.

    Pass A (blind gathering) never lets the transcript drive any search or
    fetch decision - website derivation/fetch, industry-neutral blind search,
    a representative-name search, and no-scrape link lookups (the latter
    seeded from extra_context/documents/website text only, never the
    transcript). Pass B (candidate-input enrichment) runs if the transcript,
    extra_context, or any document was provided: it extracts factual claims
    from all three (equally weighted) and runs one targeted search per
    selected claim's own search query, deduplicated against everything Pass A
    already found. This ordering (blind first, candidate-input second) is
    what avoids transcript-anchoring bias in *what gets searched for* - it
    does not mean the two are kept apart at synthesis time; the report's
    Interview Claims section deliberately maps each claim to what Pass A/B
    found about it, in neutral (non-verification) language.

    Gathering (Pass A + Pass B combined) is capped at _GATHER_TIMEOUT_SECONDS
    wall-clock - if it doesn't finish in time, synthesis proceeds with
    whatever was gathered so far rather than blocking the request further.

    Scout only gathers and presents data - it never scores, verifies, flags,
    or compares. That is the Evaluator's job entirely.

    Synthesis failure only zeroes out `internet_findings` - sources already
    gathered (and any extracted interview_claims) are always returned, since
    they cost real API/HTTP calls and are useful even without a synthesized
    report."""
    state = scout_gather._GatherState()
    try:
        await asyncio.wait_for(
            scout_gather._gather_all(state, company_name, representative_name, extra_context, documents, transcript),
            timeout=_GATHER_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        logger.warning(
            "Scout gathering exceeded %.0fs (%s) - proceeding to synthesis with partial data.",
            _GATHER_TIMEOUT_SECONDS, exc,
        )

    # Only include keys that actually have data - an empty "targeted_search"
    # list must never appear as a biasing empty section.
    sources: dict[str, Any] = {}
    if state.pages:
        sources["pages"] = state.pages
    if state.blind_search:
        sources["blind_search"] = state.blind_search
    if state.representative_search:
        sources["representative_search"] = state.representative_search
    if state.aramco_search:
        sources["aramco_search"] = state.aramco_search
    if state.link_lookups:
        sources["link_lookups"] = state.link_lookups
    if state.targeted_search:
        sources["targeted_search"] = state.targeted_search
    if state.claims:
        sources["interview_claims"] = state.claims

    try:
        internet_findings = await _synthesize_internet_findings(company_name, state.company_website, sources)
        findings_ok = True
    except Exception as exc:
        logger.warning("Scout internet-findings synthesis failed: %s: %s", type(exc).__name__, exc)
        internet_findings = ""
        findings_ok = False

    return {
        "internet_findings": internet_findings,
        "interview_claims": state.claims,
        "sources": sources,
        "findings_ok": findings_ok,
        "company_website": state.company_website,
    }
