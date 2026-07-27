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
_MAX_EXTRA_CONTEXT_CHARS = 4_000  # intake "anything else about you or your company" text, same call
_MAX_DOCUMENT_CHARS = 4_000  # each attached document's extracted text, same call
_MAX_CLAIM_QUERY_CHARS = 200  # claim search_query used for a Pass B targeted search

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

# Subset of _BLIND_SEARCH_QUERY_TEMPLATES that ask "what's the current
# number" (revenue/employees/leadership can all go stale) rather than
# something inherently historical/evergreen (overview, reviews,
# controversies, clients) - these get settings.scout_recency_bias_time_range
# passed to web_search_client.search, the rest don't.
_RECENCY_BIASED_BLIND_SEARCH_QUERY_TEMPLATES = frozenset({
    "{name} revenue",
    "{name} employees",
    "{name} leadership",
})

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

# --- PII redaction (see _redact_pii) ---
# Regex-based only, deliberately conservative: false positives (over-
# redacting) are acceptable, false negatives are not. Applied to fetched
# page/subpage text and search result text/snippets before they're attached
# to `sources`, so a vendor evaluation report can't incidentally surface a
# third party's personal contact details.
_PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Generic business mailbox local-parts are allowed through unredacted (they
# identify a department/role, not a person).
_GENERIC_EMAIL_LOCAL_PARTS = {
    "info", "contact", "sales", "support", "hello", "admin", "press",
    "media", "careers", "jobs", "hr", "marketing", "help", "enquiries",
    "inquiries",
}
# A number + street-type keyword pattern - the weakest of the three regexes
# (address formats vary a lot internationally), but still worth catching
# common US-style addresses; misses are no worse than not redacting at all.
_ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+[A-Za-z0-9.\s]{1,30}\b(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Lane|Ln|Way|Court|Ct|Place|Pl|Suite|Ste)\.?\b",
    re.IGNORECASE,
)

# --- Same-company relevance filter (see _filter_relevant / _is_relevant_result) ---
# Cheap heuristic, no LLM call: tolerates common company-suffix/punctuation
# variance rather than requiring an exact name match. Known gap - cannot
# distinguish two different companies that share the same/similar name;
# only catches results about something with a clearly different name
# entirely (deliberately lenient, biased toward keeping borderline results).
_COMPANY_SUFFIX_RE = re.compile(r"\b(?:inc|llc|ltd|corp|corporation|company|co|group|plc|gmbh)\b\.?", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")


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


def _redact_email_match(match: "re.Match[str]") -> str:
    """Redaction callback for _EMAIL_RE.sub: leaves a generic business
    mailbox (info@, contact@, etc. - see _GENERIC_EMAIL_LOCAL_PARTS)
    unredacted, replaces everything else with "[redacted]"."""
    local_part = match.group(0).split("@", 1)[0].lower()
    if local_part in _GENERIC_EMAIL_LOCAL_PARTS:
        return match.group(0)
    return "[redacted]"


def _redact_pii(text: str | None) -> str | None:
    """Redacts PII-looking substrings (phone numbers, personal email
    addresses, street-address-like patterns) from fetched/scraped text
    before it's attached to `sources`, so a vendor evaluation report can't
    incidentally surface a third party's personal contact details. Regex-
    based only (no LLM call) - a match is replaced in place with
    "[redacted]" rather than dropping the whole result. Does not attempt to
    distinguish the vendor representative's own info from a third party's -
    both are redacted. Toggled off entirely via settings.scout_redact_pii.
    Returns text unchanged if None or disabled."""
    if text is None or not settings.scout_redact_pii:
        return text
    redacted = _PHONE_RE.sub("[redacted]", text)
    redacted = _EMAIL_RE.sub(_redact_email_match, redacted)
    redacted = _ADDRESS_RE.sub("[redacted]", redacted)
    return redacted


def _normalize_company_tokens(name: str | None) -> set[str]:
    """Returns the lowercase, suffix-stripped, punctuation-stripped
    significant tokens of a company/person name (e.g. "Acme Corp, Inc." ->
    {"acme"}), used by _is_relevant_result's lenient same-company check.
    Tolerates common company-suffix/punctuation variance without needing an
    exact match. Returns an empty set for a blank/None name."""
    if not name:
        return set()
    lowered = _COMPANY_SUFFIX_RE.sub(" ", name.lower())
    lowered = _NON_ALNUM_RE.sub(" ", lowered)
    return {token for token in lowered.split() if token}


def _is_relevant_result(result: dict[str, Any], company_tokens: set[str], extra_tokens: set[str] = frozenset()) -> bool:
    """Cheap, lenient same-company relevance check (no LLM call): keeps a
    result unless NONE of the given tokens appear anywhere in its title,
    snippet, or domain - deliberately biased toward keeping borderline
    results (see _normalize_company_tokens for the tolerant name
    normalization). `extra_tokens` lets a caller OR in a second name (e.g.
    the representative's own name for representative_search, where a
    legitimate result - a person's LinkedIn profile - often doesn't repeat
    the company name at all). Known gap: cannot distinguish two different
    companies that share the same/similar name."""
    combined_tokens = company_tokens | extra_tokens
    if not combined_tokens:
        return True
    haystack = " ".join(
        [result.get("title") or "", result.get("snippet") or "", _domain_of(result.get("url") or "")]
    ).lower()
    return any(token in haystack for token in combined_tokens)


def _filter_relevant(
    results: list[dict[str, Any]], company_name: str, extra_name: str | None = None
) -> list[dict[str, Any]]:
    """Applies _is_relevant_result across a result list, dropping results
    that clearly aren't about `company_name` (optionally also matching
    `extra_name`, e.g. a representative's own name). Used for blind_search,
    representative_search, and targeted_search - NOT link_lookups, which
    are already domain-scoped to a specific discovered URL."""
    company_tokens = _normalize_company_tokens(company_name)
    extra_tokens = _normalize_company_tokens(extra_name)
    return [result for result in results if _is_relevant_result(result, company_tokens, extra_tokens)]


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

    if "representative_search" in sources:
        # Same {"query", "results"} shape as blind_search - public info about
        # the company representative (LinkedIn, bio, background), not the company.
        truncated["representative_search"] = [
            {
                **entry,
                "results": [
                    {**result, "text": _trunc(result.get("text"), _MAX_SEARCH_RESULT_CHARS)}
                    for result in (entry.get("results") or [])
                ],
            }
            for entry in sources["representative_search"]
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
    result - never fatal. Both the fetched text and the existing snippet are
    passed through _redact_pii before being attached - this is the one place
    every Scout call site's search results flow through (blind search,
    representative search, targeted claim search, no-scrape link lookups),
    so redaction applies uniformly without duplicating the call at each
    site."""
    enriched = []
    for index, result in enumerate(results):
        text = await page_fetcher.fetch_page_text(result["url"]) if index < _RELATED_FETCH_LIMIT else None
        enriched.append({**result, "snippet": _redact_pii(result.get("snippet")), "text": _redact_pii(text)})
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
    no reason to serialize them) rather than looping one at a time. Each
    query's hits are passed through _filter_relevant before enrichment, to
    drop results that clearly aren't about `company_name` (a common company
    name can otherwise pull in an unrelated company's data). The revenue/
    employees/leadership queries (_RECENCY_BIASED_BLIND_SEARCH_QUERY_TEMPLATES)
    also pass settings.scout_recency_bias_time_range, biasing which results
    Tavily returns toward the past year - this does not attach a date label
    to any result, since Tavily doesn't provide one. Returns one
    {"query", "results"} entry per query that returned at least one
    relevant hit."""
    raw_results = await asyncio.gather(
        *(
            web_search_client.search(
                template.format(name=company_name),
                time_range=settings.scout_recency_bias_time_range
                if template in _RECENCY_BIASED_BLIND_SEARCH_QUERY_TEMPLATES
                else None,
            )
            for template in _BLIND_SEARCH_QUERY_TEMPLATES
        ),
        return_exceptions=True,
    )

    entries: list[dict[str, Any]] = []
    for template, hits in zip(_BLIND_SEARCH_QUERY_TEMPLATES, raw_results):
        query = template.format(name=company_name)
        if isinstance(hits, BaseException):
            logger.warning("Blind search failed for query %r: %s", query, hits)
            continue
        relevant_hits = _filter_relevant(hits, company_name)
        if relevant_hits:
            entries.append({"query": query, "results": await _enrich_with_full_text(relevant_hits)})
    return entries


def _is_excluded_website_domain(domain: str, excluded_domains: set[str]) -> bool:
    """True if `domain` is an exact match in `excluded_domains`, or a
    subdomain of one of settings.scout_reference_site_domains (e.g.
    "en.wikipedia.org" matches "wikipedia.org") - third-party reference/
    aggregator sites that are scrapeable but are never a company's own
    official site, unlike the login-walled/scraper-blocking domains in
    `excluded_domains` itself."""
    if domain in excluded_domains:
        return True
    return any(
        domain == reference or domain.endswith(f".{reference}")
        for reference in settings.scout_reference_site_domains
    )


async def _derive_company_website(
    company_name: str, extra_context: str | None, documents: list[str] | None
) -> str | None:
    """There is no dedicated website field on the intake form - Scout derives
    it itself. First choice: any URL literally mentioned in the free-text
    "anything else about you or your company" field or an attached document
    (e.g. a company profile PDF that states its own URL) - EXCLUDING GitHub,
    any of settings.no_scrape_domains (LinkedIn, X/Twitter, etc.), and any of
    settings.scout_reference_site_domains (Wikipedia, etc.) - see
    _is_excluded_website_domain. GitHub/no_scrape domains are profile/social
    links handled by their own dedicated gathering (_maybe_gather_github /
    _gather_link_lookups); reference-site domains are third-party
    encyclopedic/aggregator sites, never the company's own. Falls back to a
    Tavily search for "{company_name} official website", skipping any hit
    on an excluded domain by the same rule - a company's LinkedIn page (or a
    Wikipedia article about it) frequently outranks its actual site for a
    generic "official website" query (especially for smaller companies),
    and letting either through here would mislabel it as the company's own
    site (fetched directly via page_fetcher, rather than the safer
    search-based _lookup_no_scrape_link approach used everywhere else for
    login-walled domains). Returns None if neither approach turns up
    anything - including when every search hit is excluded - the rest of
    the pipeline degrades gracefully without a website."""
    excluded_domains = {"github.com", *settings.no_scrape_domains}
    mentioned = [
        url for url in _extract_urls([extra_context, *(documents or [])])
        if not _is_excluded_website_domain(_domain_of(url), excluded_domains)
    ]
    if mentioned:
        return mentioned[0]

    hits = await web_search_client.search(f"{company_name} official website")
    for hit in hits:
        domain = _domain_of(hit["url"])
        if domain and not _is_excluded_website_domain(domain, excluded_domains):
            return f"https://{domain}"
    return None


async def _gather_representative_search(representative_name: str, company_name: str) -> list[dict[str, Any]]:
    """One search for public info about the interviewed company
    representative (LinkedIn, bio, background) alongside the company name,
    since the representative's identity is now a core intake input. Returns
    a single {"query", "results"} entry (same shape as a blind_search entry,
    reusing its truncation/synthesis handling) or [] if nothing came back or
    survives relevance filtering.

    Relevance filtering here ORs in the representative's own name alongside
    company_name (see _is_relevant_result's extra_tokens) - a legitimate
    result (e.g. a person's LinkedIn profile) often doesn't repeat the
    company name in its title/snippet at all, so requiring company_name
    alone would drop good results; requiring either name present still
    catches a result that's about neither."""
    if not representative_name or not representative_name.strip():
        return []
    query = f"{representative_name} {company_name}"
    hits = await web_search_client.search(query)
    if not hits:
        return []
    relevant_hits = _filter_relevant(hits, company_name, extra_name=representative_name)
    if not relevant_hits:
        return []
    return [{"query": query, "results": await _enrich_with_full_text(relevant_hits)}]


async def _maybe_gather_github(discovered_urls: list[str]) -> list[dict[str, Any]]:
    """GitHub gathering is conditional: only called for github.com URLs
    actually discovered in extra_context, an attached document, or the
    fetched website (never the transcript - see _run_pass_a). Returns an
    empty list (caller omits the key entirely) when nothing to look up -
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
    whatever login-walled URLs were discovered in extra_context, an attached
    document, or the website text (never the transcript). Returns one entry
    per matching URL."""
    lookups = []
    for url in discovered_urls:
        domain = _domain_of(url)
        if domain in settings.no_scrape_domains:
            lookups.append(await _lookup_no_scrape_link(url, domain))
    return lookups


def _combine_claim_sources(transcript: str | None, extra_context: str | None, documents: list[str] | None) -> str:
    """Builds the single user-message text handed to claim extraction, with
    each candidate-provided input labeled by source so the model can tag
    where a claim came from. Sections are omitted entirely when blank -
    a representative who only filled in the intake form (no transcript yet)
    still gets their extra_context/documents claims extracted."""
    sections: list[str] = []
    if transcript and transcript.strip():
        sections.append("=== Interview Transcript ===\n" + transcript.strip()[:_MAX_TRANSCRIPT_CHARS])
    if extra_context and extra_context.strip():
        sections.append(
            "=== Additional Context (from the company representative) ===\n"
            + extra_context.strip()[:_MAX_EXTRA_CONTEXT_CHARS]
        )
    for index, doc in enumerate(documents or [], start=1):
        if doc and doc.strip():
            sections.append(f"=== Attached Document {index} ===\n" + doc.strip()[:_MAX_DOCUMENT_CHARS])
    return "\n\n".join(sections)


def _parse_claims(content: str) -> list[dict[str, Any]]:
    """Parses the JSON claims list out of a Gemini response, tolerating
    markdown code fences the model sometimes wraps the JSON in. Each item is
    expected to be {"claim", "search_query", "source"}; a bare string is
    tolerated too (search_query falls back to the claim text itself, source
    to "transcript") in case the model ignores the object shape. Returns the
    non-empty, stripped claim objects."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    parsed = json.loads(text)
    raw_claims = parsed.get("claims", [])

    claims: list[dict[str, Any]] = []
    for item in raw_claims:
        if isinstance(item, dict):
            claim_text = item.get("claim")
            if not isinstance(claim_text, str) or not claim_text.strip():
                continue
            search_query = item.get("search_query")
            search_query = search_query.strip() if isinstance(search_query, str) and search_query.strip() else claim_text.strip()
            source = item.get("source")
            source = source.strip() if isinstance(source, str) and source.strip() else "transcript"
            claims.append({"claim": claim_text.strip(), "search_query": search_query, "source": source})
        elif isinstance(item, str) and item.strip():
            claims.append({"claim": item.strip(), "search_query": item.strip(), "source": "transcript"})
    return claims


async def _extract_claims(transcript: str | None, extra_context: str | None, documents: list[str] | None) -> list[dict[str, Any]]:
    """Pass B, step 1: one Gemini call that pulls concrete, checkable factual
    claims out of the transcript, the intake form's free-text "anything else"
    field, and any attached documents - all three are candidate-provided
    statements about the company that should be checked against public data,
    treated as equally-weighted inputs. Skips opinions, plans, and vague/
    unfalsifiable statements. Covers ANY factual topic (products/services,
    workforce, clients, revenue, offices, ownership, milestones), not just
    technical ones, so this works for any industry. Returns the extracted
    claims (each with its own short web-search query and source label), or []
    on any error or when none of the three inputs were provided (soft-fails
    so a claim-extraction failure can never abort the whole run)."""
    combined = _combine_claim_sources(transcript, extra_context, documents)
    if not settings.gemini_api_key or not combined.strip():
        return []

    payload = {
        "model": settings.scout_gemini_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Extract a JSON list of concrete, checkable factual claims "
                    "the company representative made, drawn from the labeled "
                    "sections below (an interview transcript, additional "
                    "free-text context they provided, and/or attached "
                    "documents - not every section will be present). Treat "
                    "claims from every section as equally weighted candidate "
                    "claims about the company. A factual claim is something "
                    "that could in principle be looked up or confirmed - e.g. "
                    "a specific number, date, name, product or service, "
                    "client, office location, milestone, industry ranking, "
                    "award, certification, or partnership. A ranking or award "
                    "claim (e.g. 'we were ranked #1 by Gartner in 2025') IS a "
                    "checkable factual claim, not an opinion - include it. "
                    "SKIP opinions, plans, intentions, and vague or "
                    "unfalsifiable statements (e.g. 'we care about quality' or "
                    "'we plan to grow'). Extract claims about ANY factual topic "
                    "- products or services, workforce, clients, revenue, "
                    "offices, ownership, milestones, rankings, awards, "
                    "certifications, partnerships, etc. - not only technical "
                    "topics; this company may not be a technology company at "
                    "all. For each claim also produce a short web-search "
                    "phrase (3 to 5 words) that would help verify it, and tag "
                    "which section it came from. Respond with STRICTLY a "
                    "single JSON object (no prose, no markdown fences) of "
                    'exactly this shape: {"claims": [{"claim": "<claim text>", '
                    '"search_query": "<3-5 word search phrase>", "source": '
                    '"<transcript|context|document>"}, ...]}. If there are no '
                    'extractable factual claims, return {"claims": []}.'
                ),
            },
            {"role": "user", "content": combined},
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
        logger.info("Extracted %d claim(s)", len(claims))
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


def _select_targeted_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Caps Pass B at _MAX_TARGETED_CLAIMS searches, keeping the most
    specific/verifiable claims (see _claim_specificity_score, scored on each
    claim's text). Returns the selected claim objects in their original
    extraction order."""
    if len(claims) <= _MAX_TARGETED_CLAIMS:
        return claims
    ranked = sorted(enumerate(claims), key=lambda pair: (-_claim_specificity_score(pair[1]["claim"]), pair[0]))
    selected_indices = sorted(index for index, _ in ranked[:_MAX_TARGETED_CLAIMS])
    return [claims[index] for index in selected_indices]


async def _gather_targeted(
    claims: list[dict[str, Any]], already_fetched_urls: set[str], company_name: str
) -> list[dict[str, Any]]:
    """Pass B, step 2: one focused Tavily query per selected claim (capped at
    _MAX_TARGETED_CLAIMS, see _select_targeted_claims), using each claim's own
    extracted search_query rather than a generic "{company} {claim}" string,
    all fired concurrently rather than looped one at a time. Each query's
    hits are passed through _filter_relevant BEFORE slicing to
    _TARGETED_SEARCH_MAX_RESULTS (so an irrelevant hit can't occupy one of
    the few slots kept) and filtered against everything Pass A (or an
    earlier claim here) already surfaced - the two-pass design avoids
    transcript-anchoring bias, but re-listing the exact same source twice is
    just noise, not a new finding. Returns one entry per selected claim
    (even with empty results) so the synthesis prompt can explicitly call
    out claims with no public data at all (Data Gaps section)."""
    selected = _select_targeted_claims(claims)

    async def _search_claim(claim_obj: dict[str, Any]) -> list[dict[str, Any]]:
        """Runs one targeted Tavily search for a claim's search_query, relevance-filtered then sliced to _TARGETED_SEARCH_MAX_RESULTS hits."""
        hits = await web_search_client.search(claim_obj["search_query"][:_MAX_CLAIM_QUERY_CHARS])
        return _filter_relevant(hits, company_name)[:_TARGETED_SEARCH_MAX_RESULTS]

    raw_results = await asyncio.gather(*(_search_claim(claim_obj) for claim_obj in selected), return_exceptions=True)

    targeted: list[dict[str, Any]] = []
    for claim_obj, hits in zip(selected, raw_results):
        if isinstance(hits, BaseException):
            logger.warning("Targeted search failed for claim %r: %s", claim_obj["claim"], hits)
            hits = []
        fresh_hits = [hit for hit in hits if hit["url"] not in already_fetched_urls]
        already_fetched_urls.update(hit["url"] for hit in fresh_hits)
        targeted.append({"claim": claim_obj["claim"], "results": await _enrich_with_full_text(fresh_hits)})
    return targeted


class _GatherState:
    """Mutable container for partial gathering progress. Written to
    incrementally so that if the overall _GATHER_TIMEOUT_SECONDS deadline is
    hit, whatever phases already finished are still available to hand off to
    synthesis, instead of losing everything to a cancelled coroutine."""

    def __init__(self) -> None:
        self.company_website: str | None = None
        self.pages: list[dict[str, Any]] = []
        self.blind_search: list[dict[str, Any]] = []
        self.representative_search: list[dict[str, Any]] = []
        self.github_profiles: list[dict[str, Any]] = []
        self.link_lookups: list[dict[str, Any]] = []
        self.claims: list[str] = []
        self.targeted_search: list[dict[str, Any]] = []
        self.already_fetched_urls: set[str] = set()


async def _run_pass_a(
    state: _GatherState,
    company_name: str,
    representative_name: str,
    extra_context: str | None,
    documents: list[str] | None,
) -> None:
    """Pass A: blind gathering, transcript NOT used for any search/fetch
    decision here. There is no dedicated website field on the intake form -
    the company website is derived first (see _derive_company_website), then
    its fetch, the 8 blind-search queries, and the representative search all
    run concurrently - they're independent of each other. Writes results
    into `state`; returns nothing."""
    company_website = await _derive_company_website(company_name, extra_context, documents)
    state.company_website = company_website

    page_data, blind_search, representative_search = await asyncio.gather(
        _maybe_fetch_website(company_website),
        _gather_blind_search(company_name),
        _gather_representative_search(representative_name, company_name),
    )

    if page_data:
        # The company's own derived website - the most credible source
        # available (see scout_system_prompt's source_tier/VERIFIED-label
        # criteria); tagged on both the top-level page and its subpages,
        # which are the same site. Text is passed through _redact_pii here
        # too - the only fetched text that doesn't flow through
        # _enrich_with_full_text (it comes from page_fetcher directly, not
        # a web_search_client result).
        state.pages.append({
            "url": company_website,
            "text": _redact_pii(page_data["text"]),
            "source_tier": "official",
            "subpages": [
                {**subpage, "text": _redact_pii(subpage["text"]), "source_tier": "official"}
                for subpage in page_data["subpages"]
            ],
        })
        state.already_fetched_urls.add(company_website)
        state.already_fetched_urls.update(subpage["url"] for subpage in page_data["subpages"])

    state.blind_search = blind_search
    for entry in blind_search:
        state.already_fetched_urls.update(result["url"] for result in entry["results"])

    state.representative_search = representative_search
    for entry in representative_search:
        state.already_fetched_urls.update(result["url"] for result in entry["results"])

    # extra_context/documents/website text are scanned here only for literal
    # URL mentions (e.g. "our GitHub is at ...") - this is plain-text link
    # discovery, not an analysis of what's claimed, so it doesn't cross the
    # Pass A/B line. The transcript is deliberately excluded - Pass A must
    # never let the transcript drive a search/fetch decision.
    text_blobs = [extra_context, *(documents or [])]
    for page in state.pages:
        text_blobs.append(page["text"])
        text_blobs.extend(subpage["text"] for subpage in page["subpages"])
    discovered_urls = _extract_urls(text_blobs)

    state.github_profiles = await _maybe_gather_github(discovered_urls)

    state.link_lookups = await _gather_link_lookups(discovered_urls)
    for lookup in state.link_lookups:
        state.already_fetched_urls.add(lookup["url"])
        state.already_fetched_urls.update(result["url"] for result in lookup["results"])


async def _run_pass_b(
    state: _GatherState,
    company_name: str,
    transcript: str | None,
    extra_context: str | None,
    documents: list[str] | None,
) -> None:
    """Pass B: enrichment from candidate-provided inputs - the transcript,
    the intake form's free-text context, and any attached documents - all
    treated as equally-weighted sources of claims to check. Runs only if at
    least one of the three was actually provided. Writes results into
    `state`; returns nothing."""
    has_input = (
        (transcript and transcript.strip())
        or (extra_context and extra_context.strip())
        or any(doc and doc.strip() for doc in (documents or []))
    )
    if not has_input:
        return
    extracted = await _extract_claims(transcript, extra_context, documents)
    state.claims = [claim_obj["claim"] for claim_obj in extracted]
    if extracted:
        state.targeted_search = await _gather_targeted(extracted, state.already_fetched_urls, company_name)


async def _gather_all(
    state: _GatherState,
    company_name: str,
    representative_name: str,
    extra_context: str | None,
    documents: list[str] | None,
    transcript: str | None,
) -> None:
    """Runs Pass A then Pass B sequentially, writing results into `state` as
    it goes (Pass B needs Pass A's already_fetched_urls for dedup). Returns
    nothing."""
    await _run_pass_a(state, company_name, representative_name, extra_context, documents)
    await _run_pass_b(state, company_name, transcript, extra_context, documents)


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
    representative_name: str,
    representative_role: str | None,
    extra_context: str | None,
    documents: list[str] | None,
    transcript: str | None,
) -> dict[str, Any]:
    """Orchestrates the two-pass Data Scout Agent pipeline. Returns
    {"internet_findings": str, "interview_claims": list[str],
    "sources": dict, "findings_ok": bool, "company_website": str | None}.

    There is no dedicated website field on the intake form anymore - Pass A
    derives it itself (see _derive_company_website) from any URL mentioned in
    extra_context/documents, falling back to a Tavily search for the
    company's official website.

    Pass A (blind gathering) never lets the transcript drive any search or
    fetch decision - website derivation/fetch, industry-neutral blind search,
    a representative-name search, conditional GitHub, and no-scrape link
    lookups (the latter two seeded from extra_context/documents/website text
    only, never the transcript). Pass B (candidate-input enrichment) runs if
    the transcript, extra_context, or any document was provided: it extracts
    factual claims from all three (equally weighted) and runs one targeted
    search per selected claim's own search query, deduplicated against
    everything Pass A already found. This ordering (blind first,
    candidate-input second) is what avoids transcript-anchoring bias in
    *what gets searched for* - it does not mean the two are kept apart at
    synthesis time; the report's Interview Claims section deliberately maps
    each claim to what Pass A/B found about it, in neutral (non-
    verification) language.

    Gathering (Pass A + Pass B combined) is capped at _GATHER_TIMEOUT_SECONDS
    wall-clock - if it doesn't finish in time, synthesis proceeds with
    whatever was gathered so far rather than blocking the request further.

    representative_role is accepted for future use but is not currently
    referenced in gathering or synthesis.

    Scout only gathers and presents data - it never scores, verifies, flags,
    or compares. That is the Evaluator's job entirely.

    Synthesis failure only zeroes out `internet_findings` - sources already
    gathered (and any extracted interview_claims) are always returned, since
    they cost real API/HTTP calls and are useful even without a synthesized
    report."""
    del representative_role  # accepted, not yet used

    state = _GatherState()
    try:
        await asyncio.wait_for(
            _gather_all(state, company_name, representative_name, extra_context, documents, transcript),
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
    if state.representative_search:
        sources["representative_search"] = state.representative_search
    if state.github_profiles:
        sources["github"] = state.github_profiles
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
