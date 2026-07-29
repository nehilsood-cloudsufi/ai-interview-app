"""Data Scout Agent gathering: all network I/O (Tavily search, page fetches,
Gemini claim extraction) across Pass A (blind, transcript-independent) and
Pass B (candidate-input enrichment). Orchestrated by data_scout_agent.run_scout.
"""

import asyncio
import logging
from typing import Any

import httpx

from app.config import settings
from app.services import page_fetcher, scout_utils, web_search_client

logger = logging.getLogger(__name__)

_RELATED_FETCH_LIMIT = 1  # how many of a search's top results get their full page text fetched (biggest single time sink in the pipeline)
_CLAIM_EXTRACTION_TIMEOUT_SECONDS = 30.0  # httpx timeout for the single-attempt claim-extraction call
_MAX_CLAIM_QUERY_CHARS = 200  # claim search_query used for a Pass B targeted search
_TARGETED_SEARCH_MAX_RESULTS = 3  # Pass B results sliced to this many client-side, keeping the per-claim source list tight

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


def _gemini_chat_completions_url() -> str:
    return f"{settings.gemini_base_url}chat/completions"


def _gemini_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.gemini_api_key}",
        "Content-Type": "application/json",
    }


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
        enriched.append({**result, "snippet": scout_utils._redact_pii(result.get("snippet")), "text": scout_utils._redact_pii(text)})
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
        relevant_hits = scout_utils._filter_relevant(hits, company_name)
        if relevant_hits:
            entries.append({"query": query, "results": await _enrich_with_full_text(relevant_hits)})
    return entries


async def _derive_company_website(
    company_name: str, extra_context: str | None, documents: list[str] | None
) -> str | None:
    """There is no dedicated website field on the intake form - Scout derives
    it itself. First choice: any URL literally mentioned in the free-text
    "anything else about you or your company" field or an attached document
    (e.g. a company profile PDF that states its own URL) - EXCLUDING GitHub,
    any of settings.no_scrape_domains (LinkedIn, X/Twitter, etc.), and any of
    settings.scout_reference_site_domains (Wikipedia, etc.) - see
    _is_excluded_website_domain. github.com is excluded outright (a GitHub
    org page is never a company's own official site, same reasoning as
    LinkedIn); no_scrape_domains links get their own dedicated gathering via
    _gather_link_lookups; reference-site domains are third-party
    encyclopedic/aggregator sites, never the company's own. Falls back to a
    Tavily search for "{company_name} official website" - each hit must pass
    BOTH the exclusion check (not on an excluded domain, same rule as above)
    AND the same-company relevance check (_is_relevant_result, the exact
    heuristic used for blind_search/representative_search/targeted_search -
    see _normalize_company_tokens) before being accepted. The exclusion list
    alone caught known bad categories (a company's LinkedIn page or Wikipedia
    article frequently outranks its actual site for a generic "official
    website" query) but nothing stops Tavily's ranking from surfacing an
    entirely unrelated, uncategorized site that just isn't on that list - two
    real cases found in testing: an unrelated company sharing a short/generic
    name, and a completely unrelated organization sharing no name overlap at
    all. The relevance check catches both, since neither hit's title/snippet/
    domain shares any token with company_name.

    A third case survives both checks legitimately: a third party can host a
    page that genuinely IS about the company (passes relevance) on a domain
    that isn't the company's own and isn't on any blocklist (e.g. a big
    partner's directory listing) - relevance answers "is this content about
    the company," not "does this domain belong to the company," so nothing
    above catches it. Among hits that already pass both checks, one more
    signal breaks the tie: PREFERRED (domain itself contains a company-name
    token, e.g. "persistent.com" for "Persistent Systems") beats ACCEPTABLE
    (relevant content, but the domain doesn't match, e.g. a partner's
    "ibm.com" page about the company). The first PREFERRED hit found wins
    outright, regardless of Tavily's ranking; ACCEPTABLE is only a fallback
    when no hit anywhere in the results is PREFERRED - this is a tiebreaker
    among already-accepted hits, not a stricter filter, so a relevant hit on
    a non-matching domain is still used rather than discarded down to None.

    Returns None if neither approach turns up anything - including when
    every search hit is excluded or irrelevant - the rest of the pipeline
    degrades gracefully without a website."""
    excluded_domains = {"github.com", *settings.no_scrape_domains}
    mentioned = [
        url for url in scout_utils._extract_urls([extra_context, *(documents or [])])
        if not scout_utils._is_excluded_website_domain(scout_utils._domain_of(url), excluded_domains)
    ]
    if mentioned:
        return mentioned[0]

    company_tokens = scout_utils._normalize_company_tokens(company_name)
    hits = await web_search_client.search(f"{company_name} official website")
    acceptable_domain: str | None = None
    for hit in hits:
        domain = scout_utils._domain_of(hit["url"])
        if (
            not domain
            or scout_utils._is_excluded_website_domain(domain, excluded_domains)
            or not scout_utils._is_relevant_result(hit, company_tokens)
        ):
            continue
        if any(token in domain for token in company_tokens):
            return f"https://{domain}"
        if acceptable_domain is None:
            acceptable_domain = domain
    if acceptable_domain:
        return f"https://{acceptable_domain}"
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
    relevant_hits = scout_utils._filter_relevant(hits, company_name, extra_name=representative_name)
    if not relevant_hits:
        return []
    return [{"query": query, "results": await _enrich_with_full_text(relevant_hits)}]


async def _gather_link_lookups(discovered_urls: list[str]) -> list[dict[str, Any]]:
    """Same no-scrape-domain targeted-search logic as before, applied to
    whatever login-walled URLs were discovered in extra_context, an attached
    document, or the website text (never the transcript). Returns one entry
    per matching URL."""
    lookups = []
    for url in discovered_urls:
        domain = scout_utils._domain_of(url)
        if domain in settings.no_scrape_domains:
            lookups.append(await _lookup_no_scrape_link(url, domain))
    return lookups


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
    combined = scout_utils._combine_claim_sources(transcript, extra_context, documents)
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
        claims = scout_utils._parse_claims(content)
        logger.info("Extracted %d claim(s)", len(claims))
        return claims
    except Exception as exc:
        logger.warning("Claim extraction failed: %s", exc)
        return []


async def _gather_targeted(
    claims: list[dict[str, Any]], already_fetched_urls: set[str], company_name: str
) -> list[dict[str, Any]]:
    """Pass B, step 2: one focused Tavily query per selected claim (capped at
    scout_utils._MAX_TARGETED_CLAIMS, see scout_utils._select_targeted_claims),
    using each claim's own extracted search_query rather than a generic
    "{company} {claim}" string, all fired concurrently rather than looped one
    at a time. Each query's hits are passed through _filter_relevant BEFORE
    slicing to _TARGETED_SEARCH_MAX_RESULTS (so an irrelevant hit can't
    occupy one of the few slots kept) and filtered against everything Pass A
    (or an earlier claim here) already surfaced - the two-pass design avoids
    transcript-anchoring bias, but re-listing the exact same source twice is
    just noise, not a new finding. Returns one entry per selected claim
    (even with empty results) so the synthesis prompt can explicitly call
    out claims with no public data at all (Data Gaps section)."""
    selected = scout_utils._select_targeted_claims(claims)

    async def _search_claim(claim_obj: dict[str, Any]) -> list[dict[str, Any]]:
        hits = await web_search_client.search(claim_obj["search_query"][:_MAX_CLAIM_QUERY_CHARS])
        return scout_utils._filter_relevant(hits, company_name)[:_TARGETED_SEARCH_MAX_RESULTS]

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
    incrementally so that if the overall gather deadline (see
    data_scout_agent._GATHER_TIMEOUT_SECONDS) is hit, whatever phases already
    finished are still available to hand off to synthesis, instead of losing
    everything to a cancelled coroutine."""

    def __init__(self) -> None:
        self.company_website: str | None = None
        self.pages: list[dict[str, Any]] = []
        self.blind_search: list[dict[str, Any]] = []
        self.representative_search: list[dict[str, Any]] = []
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
            "text": scout_utils._redact_pii(page_data["text"]),
            "source_tier": "official",
            "subpages": [
                {**subpage, "text": scout_utils._redact_pii(subpage["text"]), "source_tier": "official"}
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
    # URL mentions (e.g. "our LinkedIn is at ...") - this is plain-text link
    # discovery, not an analysis of what's claimed, so it doesn't cross the
    # Pass A/B line. The transcript is deliberately excluded - Pass A must
    # never let the transcript drive a search/fetch decision.
    text_blobs = [extra_context, *(documents or [])]
    for page in state.pages:
        text_blobs.append(page["text"])
        text_blobs.extend(subpage["text"] for subpage in page["subpages"])
    discovered_urls = scout_utils._extract_urls(text_blobs)

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
