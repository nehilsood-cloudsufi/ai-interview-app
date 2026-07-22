import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.services import github_client, page_fetcher, web_search_client

logger = logging.getLogger(__name__)

# --- Gemini call tuning ---
# Gemini occasionally returns 5xx (server-side overload) on large/complex
# requests like the synthesis call - these are transient, unlike 4xx (bad
# request/auth/quota), which retrying can't fix. A couple of short-backoff
# retries clears most of them without meaningfully slowing down the common
# case. Claim extraction has no retry loop: it soft-fails to [] on any
# error (see _extract_claims), so a single attempt is enough.
_SYNTHESIS_MAX_ATTEMPTS = 3  # total attempts for the internet-findings synthesis call
_SYNTHESIS_RETRY_BACKOFF_SECONDS = 3.0  # delay between synthesis retry attempts
_SYNTHESIS_TIMEOUT_SECONDS = 180.0  # per-attempt httpx timeout; large payloads take a while to process
_CLAIM_EXTRACTION_TIMEOUT_SECONDS = 30.0  # httpx timeout for the single-attempt claim-extraction call

# --- Payload truncation ---
# Per-field character caps applied before the sources dict is serialised and
# sent to Gemini. The gathered page/search text is the primary cause of
# ReadTimeout: a single company page alone can be 30 000+ chars, and with
# three subpages plus enriched search results the JSON payload can easily
# exceed 200 000 chars - Gemini Flash needs 60-120 s to process that even
# before it starts generating, which blows through any reasonable httpx
# timeout. These caps keep the total payload manageable while preserving the
# most informative content (the opening of each text block).
_MAX_PAGE_TEXT_CHARS = 3_000  # main page body
_MAX_SUBPAGE_TEXT_CHARS = 1_500  # each same-site subpage
_MAX_SEARCH_RESULT_CHARS = 1_000  # each enriched search-result text block
_MAX_TRANSCRIPT_CHARS = 8_000  # transcript text passed to the (Pass B only) claim-extraction call
_MAX_CLAIM_QUERY_CHARS = 200  # claim text used when building a Pass B targeted search query

# --- Pass A / Pass B tuning ---
_RELATED_FETCH_LIMIT = 1  # how many of a search's top results get their full page text fetched (biggest single time sink in the pipeline)
_MAX_TARGETED_CLAIMS = 10  # Pass B is capped at this many targeted searches regardless of how many claims were extracted (see _select_targeted_claims)
_TARGETED_SEARCH_MAX_RESULTS = 3  # Pass B results sliced to this many client-side, keeping the per-claim source list tight
_GATHER_TIMEOUT_SECONDS = 45.0  # total wall-clock budget for Pass A + Pass B combined before synthesis proceeds with partial data

_URL_RE = re.compile(r"https?://\S+")  # matches a bare URL in free text (transcript or fetched page text)

# Industry-neutral blind-gathering queries (Pass A). Deliberately excludes
# tech-specific terms ("tech stack", "engineering") so this works equally
# well for a software firm, a law practice, a construction company, a
# restaurant chain, or a healthcare provider.
_BLIND_SEARCH_QUERY_TEMPLATES = (
    "{name} overview",
    "{name} latest news",
    "{name} revenue",
    "{name} employees",
    "{name} leadership",
    "{name} reviews",
    "{name} controversies",
    "{name} clients",
)

# --- Claim-selection heuristic (see _claim_specificity_score / _select_targeted_claims) ---
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")  # a 4-digit year mention - strong signal a claim is checkable
_NUMBER_RE = re.compile(r"\d")  # any digit - weaker signal than a year but still concrete
_PROPER_NOUN_RE = re.compile(r"(?<!^)(?<!\. )\b[A-Z][a-z]+")  # a capitalized word not at a sentence start - suggests a named entity
# Vague, internal-metric-style phrasing that's unlikely to ever show up in a
# public source, even if the claim itself contains a number (e.g. "our
# retention rate is 95%") - deprioritized when capping Pass B searches.
_VAGUE_CLAIM_MARKERS = (
    "retention rate", "growth rate", "growth percentage", "churn rate",
    "conversion rate", "engagement rate", "satisfaction score",
    "market share", "profit margin", "nps score", "internal metric",
)


def _gemini_chat_completions_url() -> str:
    """Returns the full URL for Gemini's OpenAI-compatible chat/completions endpoint."""
    return f"{settings.gemini_base_url}chat/completions"


def _gemini_headers() -> dict[str, str]:
    """Returns the standard Authorization/Content-Type headers for a Gemini request."""
    return {
        "Authorization": f"Bearer {settings.gemini_api_key}",
        "Content-Type": "application/json",
    }


def _domain_of(url: str) -> str:
    """Returns the lowercase registrable domain of a URL, stripping a leading "www.". """
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _extract_urls(text_blobs: list[str]) -> list[str]:
    """Finds every URL mentioned across the given text blobs (transcript,
    fetched page/subpage text) - a lightweight stand-in for the old
    file-upload link_extractor, since there's no document to extract from
    anymore. Returns a deduplicated, sorted list of URLs."""
    urls: set[str] = set()
    for blob in text_blobs:
        if not blob:
            continue
        urls.update(match.rstrip(").,;\"'") for match in _URL_RE.findall(blob))
    return sorted(urls)


def _trunc(text: str | None, limit: int) -> str | None:
    """Truncates text to `limit` characters, appending a marker so the model
    knows the content was cut. Returns None unchanged (field simply absent)."""
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[:limit] + f"… [truncated, {len(text) - limit} chars omitted]"


def _truncate_sources(sources: dict[str, Any]) -> dict[str, Any]:
    """Returns a deep-truncated copy of the sources dict so the serialised
    JSON payload stays within Gemini's practical processing window.

    Only text/content fields are touched; all structural keys (url, title,
    snippet, domain, full_profile_accessible, query, claim, etc.) are
    preserved verbatim so the synthesising prompt can still cite sources
    correctly. Only keys actually present in `sources` are copied over -
    callers are expected to have already omitted empty/inapplicable keys."""
    truncated: dict[str, Any] = {}

    if "github" in sources:
        # README excerpts are already capped at the source (github_client).
        truncated["github"] = sources["github"]

    if "interview_claims" in sources:
        # Short, LLM-extracted claim strings - no per-item cap needed, and
        # the claim-analysis synthesis section requires every one of them.
        truncated["interview_claims"] = sources["interview_claims"]

    if "pages" in sources:
        truncated["pages"] = [
            {
                **page,
                "text": _trunc(page.get("text"), _MAX_PAGE_TEXT_CHARS),
                "subpages": [
                    {**subpage, "text": _trunc(subpage.get("text"), _MAX_SUBPAGE_TEXT_CHARS)}
                    for subpage in (page.get("subpages") or [])
                ],
            }
            for page in sources["pages"]
        ]

    if "link_lookups" in sources:
        truncated["link_lookups"] = [
            {
                **lookup,
                "results": [
                    {**result, "text": _trunc(result.get("text"), _MAX_SEARCH_RESULT_CHARS)}
                    for result in (lookup.get("results") or [])
                ],
            }
            for lookup in sources["link_lookups"]
        ]

    if "blind_search" in sources:
        truncated["blind_search"] = [
            {
                **entry,
                "results": [
                    {**result, "text": _trunc(result.get("text"), _MAX_SEARCH_RESULT_CHARS)}
                    for result in (entry.get("results") or [])
                ],
            }
            for entry in sources["blind_search"]
        ]

    if "targeted_search" in sources:
        truncated["targeted_search"] = [
            {
                **entry,
                "results": [
                    {**result, "text": _trunc(result.get("text"), _MAX_SEARCH_RESULT_CHARS)}
                    for result in (entry.get("results") or [])
                ],
            }
            for entry in sources["targeted_search"]
        ]

    return truncated


async def _enrich_with_full_text(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attaches the actual page text (not just title/snippet) for the top
    _RELATED_FETCH_LIMIT search results. Returns the same results with a
    "text" field added to each; a fetch failure just leaves it None for that
    result - never fatal."""
    enriched = []
    for index, result in enumerate(results):
        text = await page_fetcher.fetch_page_text(result["url"]) if index < _RELATED_FETCH_LIMIT else None
        enriched.append({**result, "text": text})
    return enriched


async def _lookup_no_scrape_link(url: str, domain: str) -> dict[str, Any]:
    """Runs a targeted search for a login-walled platform link (LinkedIn,
    Twitter/X, etc.) instead of scraping it directly. Returns a dict with
    `full_profile_accessible` always False - the report must say this is a
    partial view, never the complete profile."""
    hits = await web_search_client.search(f"site:{domain} {url} overview")
    return {"url": url, "domain": domain, "results": await _enrich_with_full_text(hits), "full_profile_accessible": False}


async def _maybe_fetch_website(company_website: str | None) -> dict[str, Any] | None:
    """Fetches the company website (plus same-site subpages) if a URL was
    given. Returns None if no website was provided, or if the fetch failed."""
    if not company_website:
        return None
    return await page_fetcher.fetch_page_with_subpages(company_website)


async def _gather_blind_search(company_name: str) -> list[dict[str, Any]]:
    """Pass A: industry-neutral background search, independent of the
    transcript. All 8 queries fire concurrently (independent Tavily calls,
    no reason to serialize them) rather than looping one at a time. Returns
    one {"query", "results"} entry per query that returned at least one hit."""
    raw_results = await asyncio.gather(
        *(web_search_client.search(template.format(name=company_name)) for template in _BLIND_SEARCH_QUERY_TEMPLATES),
        return_exceptions=True,
    )

    entries: list[dict[str, Any]] = []
    for template, hits in zip(_BLIND_SEARCH_QUERY_TEMPLATES, raw_results):
        query = template.format(name=company_name)
        if isinstance(hits, BaseException):
            logger.warning("Blind search failed for query %r: %s", query, hits)
            continue
        if hits:
            entries.append({"query": query, "results": await _enrich_with_full_text(hits)})
    return entries


async def _maybe_gather_github(discovered_urls: list[str]) -> list[dict[str, Any]]:
    """GitHub gathering is conditional: only called for github.com URLs
    actually discovered in the transcript or on the fetched website. Returns
    an empty list (caller omits the key entirely) when nothing to look up -
    this feature must not bias against non-technical companies by emitting
    an empty "no GitHub data" section for a law firm or a restaurant chain."""
    usernames_seen: set[str] = set()
    profiles: list[dict[str, Any]] = []
    for url in discovered_urls:
        if _domain_of(url) != "github.com":
            continue
        username = github_client.derive_username(url)
        if not username or username in usernames_seen:
            continue
        usernames_seen.add(username)
        profile = await github_client.fetch_profile(username)
        if profile:
            profiles.append(profile)
    return profiles


async def _gather_link_lookups(discovered_urls: list[str]) -> list[dict[str, Any]]:
    """Same no-scrape-domain targeted-search logic as before, applied to
    whatever login-walled URLs were discovered in the transcript/website
    text (there's no uploaded document to extract links from anymore).
    Returns one entry per matching URL."""
    lookups = []
    for url in discovered_urls:
        domain = _domain_of(url)
        if domain in settings.no_scrape_domains:
            lookups.append(await _lookup_no_scrape_link(url, domain))
    return lookups


def _parse_claims(content: str) -> list[str]:
    """Parses the JSON claims list out of a Gemini response, tolerating
    markdown code fences the model sometimes wraps the JSON in. Returns the
    non-empty, stripped claim strings."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    parsed = json.loads(text)
    claims = parsed.get("claims", [])
    return [claim.strip() for claim in claims if isinstance(claim, str) and claim.strip()]


async def _extract_claims(transcript: str) -> list[str]:
    """Pass B, step 1: one Gemini call that pulls concrete, checkable factual
    claims out of the transcript - skipping opinions, plans, and vague/
    unfalsifiable statements. Covers ANY factual topic (products/services,
    workforce, clients, revenue, offices, ownership, milestones), not just
    technical ones, so this works for any industry. Returns the extracted
    claims, or [] on any error (soft-fails so a claim-extraction failure can
    never abort the whole run)."""
    if not settings.gemini_api_key or not transcript.strip():
        return []

    payload = {
        "model": settings.scout_gemini_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Extract a JSON list of concrete, checkable factual claims "
                    "the company representative made in this interview "
                    "transcript. A factual claim is something that could in "
                    "principle be looked up or confirmed - e.g. a specific "
                    "number, date, name, product or service, client, office "
                    "location, milestone, industry ranking, award, "
                    "certification, or partnership. A ranking or award claim "
                    "(e.g. 'we were ranked #1 by Gartner in 2025') IS a "
                    "checkable factual claim, not an opinion - include it. "
                    "SKIP opinions, plans, intentions, and vague or "
                    "unfalsifiable statements (e.g. 'we care about quality' or "
                    "'we plan to grow'). Extract claims about ANY factual topic "
                    "- products or services, workforce, clients, revenue, "
                    "offices, ownership, milestones, rankings, awards, "
                    "certifications, partnerships, etc. - not only technical "
                    "topics; this company may not be a technology company at "
                    "all. Respond with STRICTLY a single JSON object (no "
                    "prose, no markdown fences) of exactly this shape: "
                    '{"claims": ["<claim 1>", "<claim 2>", ...]}. If there are '
                    'no extractable factual claims, return {"claims": []}.'
                ),
            },
            {"role": "user", "content": transcript.strip()[:_MAX_TRANSCRIPT_CHARS]},
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=_CLAIM_EXTRACTION_TIMEOUT_SECONDS) as client:
            response = await client.post(
                _gemini_chat_completions_url(),
                json=payload,
                headers=_gemini_headers(),
            )
            response.raise_for_status()
            response_body = response.json()
        content = response_body["choices"][0]["message"]["content"]
        claims = _parse_claims(content)
        logger.info("Extracted %d interview claim(s)", len(claims))
        return claims
    except Exception as exc:
        logger.warning("Claim extraction failed: %s", exc)
        return []


def _claim_specificity_score(claim: str) -> int:
    """Rough, cheap heuristic (no extra LLM call, to keep Pass B fast) for how
    likely a claim is to have discoverable public data behind it. Returns a
    score prioritizing concrete, externally-checkable facts (years, numbers,
    named entities) over vague internal metrics unlikely to ever be
    published publicly."""
    lowered = claim.lower()
    if any(marker in lowered for marker in _VAGUE_CLAIM_MARKERS):
        return -1
    score = 0
    if _YEAR_RE.search(claim):
        score += 2
    if _NUMBER_RE.search(claim):
        score += 1
    if _PROPER_NOUN_RE.search(claim):
        score += 1
    return score


def _select_targeted_claims(claims: list[str]) -> list[str]:
    """Caps Pass B at _MAX_TARGETED_CLAIMS searches, keeping the most
    specific/verifiable claims (see _claim_specificity_score). Returns the
    selected claims in their original transcript order."""
    if len(claims) <= _MAX_TARGETED_CLAIMS:
        return claims
    ranked = sorted(enumerate(claims), key=lambda pair: (-_claim_specificity_score(pair[1]), pair[0]))
    selected_indices = sorted(index for index, _ in ranked[:_MAX_TARGETED_CLAIMS])
    return [claims[index] for index in selected_indices]


async def _gather_targeted(company_name: str, claims: list[str], already_fetched_urls: set[str]) -> list[dict[str, Any]]:
    """Pass B, step 2: one focused Tavily query per selected claim (capped at
    _MAX_TARGETED_CLAIMS, see _select_targeted_claims), all fired
    concurrently rather than looped one at a time. Each query's results are
    sliced to _TARGETED_SEARCH_MAX_RESULTS and filtered against everything
    Pass A (or an earlier claim here) already surfaced - the two-pass design
    avoids transcript-anchoring bias, but re-listing the exact same source
    twice is just noise, not a new finding. Returns one entry per selected
    claim (even with empty results) so the synthesis prompt can explicitly
    call out claims with no public data at all (Data Gaps section)."""
    selected = _select_targeted_claims(claims)

    async def _search_claim(claim: str) -> list[dict[str, Any]]:
        """Runs one targeted Tavily search for a claim, sliced to _TARGETED_SEARCH_MAX_RESULTS hits."""
        query = f"{company_name} {claim[:_MAX_CLAIM_QUERY_CHARS]}"
        hits = await web_search_client.search(query)
        return hits[:_TARGETED_SEARCH_MAX_RESULTS]

    raw_results = await asyncio.gather(*(_search_claim(claim) for claim in selected), return_exceptions=True)

    targeted: list[dict[str, Any]] = []
    for claim, hits in zip(selected, raw_results):
        if isinstance(hits, BaseException):
            logger.warning("Targeted search failed for claim %r: %s", claim, hits)
            hits = []
        fresh_hits = [hit for hit in hits if hit["url"] not in already_fetched_urls]
        already_fetched_urls.update(hit["url"] for hit in fresh_hits)
        targeted.append({"claim": claim, "results": await _enrich_with_full_text(fresh_hits)})
    return targeted


class _GatherState:
    """Mutable container for partial gathering progress. Written to
    incrementally so that if the overall _GATHER_TIMEOUT_SECONDS deadline is
    hit, whatever phases already finished are still available to hand off to
    synthesis, instead of losing everything to a cancelled coroutine."""

    def __init__(self) -> None:
        self.pages: list[dict[str, Any]] = []
        self.blind_search: list[dict[str, Any]] = []
        self.github_profiles: list[dict[str, Any]] = []
        self.link_lookups: list[dict[str, Any]] = []
        self.claims: list[str] = []
        self.targeted_search: list[dict[str, Any]] = []
        self.already_fetched_urls: set[str] = set()


async def _run_pass_a(state: _GatherState, company_name: str, company_website: str | None, transcript: str | None) -> None:
    """Pass A: blind gathering, transcript NOT used for any search/fetch
    decision here. Website fetch and blind search run concurrently - they're
    independent of each other. Writes results into `state`; returns nothing."""
    page_data, blind_search = await asyncio.gather(
        _maybe_fetch_website(company_website),
        _gather_blind_search(company_name),
    )

    if page_data:
        state.pages.append({"url": company_website, "text": page_data["text"], "subpages": page_data["subpages"]})
        state.already_fetched_urls.add(company_website)
        state.already_fetched_urls.update(subpage["url"] for subpage in page_data["subpages"])

    state.blind_search = blind_search
    for entry in blind_search:
        state.already_fetched_urls.update(result["url"] for result in entry["results"])

    # Transcript text is scanned here only for literal URL mentions (e.g. "our
    # GitHub is at ...") - this is plain-text link discovery, not an analysis
    # of what the transcript claims, so it doesn't cross the Pass A/B line.
    text_blobs = [transcript] if transcript else []
    for page in state.pages:
        text_blobs.append(page["text"])
        text_blobs.extend(subpage["text"] for subpage in page["subpages"])
    discovered_urls = _extract_urls(text_blobs)

    state.github_profiles = await _maybe_gather_github(discovered_urls)

    state.link_lookups = await _gather_link_lookups(discovered_urls)
    for lookup in state.link_lookups:
        state.already_fetched_urls.add(lookup["url"])
        state.already_fetched_urls.update(result["url"] for result in lookup["results"])


async def _run_pass_b(state: _GatherState, company_name: str, transcript: str | None) -> None:
    """Pass B: transcript-aware enrichment, only if a transcript was given.
    Writes results into `state`; returns nothing."""
    if not (transcript and transcript.strip()):
        return
    state.claims = await _extract_claims(transcript)
    if state.claims:
        state.targeted_search = await _gather_targeted(company_name, state.claims, state.already_fetched_urls)


async def _gather_all(state: _GatherState, company_name: str, company_website: str | None, transcript: str | None) -> None:
    """Runs Pass A then Pass B sequentially, writing results into `state` as
    it goes (Pass B needs Pass A's already_fetched_urls for dedup). Returns
    nothing."""
    await _run_pass_a(state, company_name, company_website, transcript)
    await _run_pass_b(state, company_name, transcript)


async def _post_to_gemini(payload: dict[str, Any]) -> str:
    """Sends a chat/completions payload to Gemini with retry-on-5xx/timeout
    (up to _SYNTHESIS_MAX_ATTEMPTS), raising immediately on 4xx. Returns the
    model's reply text; raises the last error if all attempts fail."""
    last_error: Exception | None = None
    for attempt in range(1, _SYNTHESIS_MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=_SYNTHESIS_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    _gemini_chat_completions_url(),
                    json=payload,
                    headers=_gemini_headers(),
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

    truncated_sources = _truncate_sources(sources)
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
    company_website: str | None,
    representative_name: str | None,
    representative_role: str | None,
    transcript: str | None,
) -> dict[str, Any]:
    """Orchestrates the two-pass Data Scout Agent pipeline. Returns
    {"internet_findings": str, "interview_claims": list[str],
    "sources": dict, "findings_ok": bool}.

    Pass A (blind gathering) never lets the transcript drive any search or
    fetch decision - website fetch, industry-neutral blind search,
    conditional GitHub, and no-scrape link lookups. Pass B (transcript-aware
    enrichment) only runs if a transcript was actually provided: it extracts
    factual claims and runs one targeted search per selected claim,
    deduplicated against everything Pass A already found. This ordering
    (blind first, transcript-aware second) is what avoids transcript-
    anchoring bias in *what gets searched for* - it does not mean the two
    are kept apart at synthesis time; the report's Claim Analysis section
    deliberately maps each claim to what Pass A/B found about it, in neutral
    (non-verification) language.

    Gathering (Pass A + Pass B combined) is capped at _GATHER_TIMEOUT_SECONDS
    wall-clock - if it doesn't finish in time, synthesis proceeds with
    whatever was gathered so far rather than blocking the request further.

    representative_name/representative_role are accepted for future use
    (e.g. attributing claims to a specific person) but are not currently
    referenced in gathering or synthesis - the transcript is the sole
    "interview" input for now, ahead of the pub/sub interview-module wiring.

    Scout only gathers and presents data - it never scores, verifies, flags,
    or compares. That is the Evaluator's job entirely.

    Synthesis failure only zeroes out `internet_findings` - sources already
    gathered (and any extracted interview_claims) are always returned, since
    they cost real API/HTTP calls and are useful even without a synthesized
    report."""
    del representative_name, representative_role  # accepted, not yet used

    state = _GatherState()
    try:
        await asyncio.wait_for(
            _gather_all(state, company_name, company_website, transcript),
            timeout=_GATHER_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        logger.warning(
            "Scout gathering exceeded %.0fs (%s) - proceeding to synthesis with partial data.",
            _GATHER_TIMEOUT_SECONDS, exc,
        )

    # Only include keys that actually have data - an empty "github" or
    # "targeted_search" list must never appear as a biasing empty section.
    sources: dict[str, Any] = {}
    if state.pages:
        sources["pages"] = state.pages
    if state.blind_search:
        sources["blind_search"] = state.blind_search
    if state.github_profiles:
        sources["github"] = state.github_profiles
    if state.link_lookups:
        sources["link_lookups"] = state.link_lookups
    if state.targeted_search:
        sources["targeted_search"] = state.targeted_search
    if state.claims:
        sources["interview_claims"] = state.claims

    try:
        internet_findings = await _synthesize_internet_findings(company_name, company_website, sources)
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
    }
