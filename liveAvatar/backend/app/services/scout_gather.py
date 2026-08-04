"""Data Scout Agent gathering: all network I/O (Tavily search, page fetches,
Gemini claim extraction) across Pass A (blind, transcript-independent) and
Pass B (candidate-input enrichment). Orchestrated by data_scout_agent.run_scout.
"""

import asyncio
import logging
from typing import Any

from app.config import settings
from app.services import gemini_client, page_fetcher, scout_utils, web_search_client

logger = logging.getLogger(__name__)

_RELATED_FETCH_LIMIT = 1  # how many of a search's top results get their full page text fetched (biggest single time sink in the pipeline)
_CLAIM_EXTRACTION_TIMEOUT_SECONDS = 60.0  # httpx timeout for the single-attempt claim-extraction call - raised from 30s (2026-08-03): a real transcript was observed exceeding 30s end-to-end, causing the call to time out and silently return zero claims instead of just running a bit longer
_MAX_CLAIM_QUERY_CHARS = 200  # claim search_query used for a Pass B targeted search
_TARGETED_SEARCH_MAX_RESULTS = 3  # Pass B results sliced to this many client-side, keeping the per-claim source list tight
_MAX_LINK_LOOKUPS = 5  # cap on no-scrape link lookups per run - footer link farms must not eat the gather budget

# Per-run page-text fetch dedup: URL -> Future of the page text. The same URL
# routinely ranks top for several blind-search queries (and may equal the
# already-fetched company website), and without this each occurrence paid a
# full fetch+parse. Futures (not values) so concurrent enrichment tasks that
# race on the same URL share ONE in-flight fetch.
_FetchCache = dict[str, "asyncio.Future[str | None]"]


async def _fetch_page_text_timed(url: str) -> str | None:
    """page_fetcher.fetch_page_text with a per-URL timing log - split out of
    _fetch_text_cached so a cache HIT (a concurrent enrichment task awaiting
    a fetch some other task already kicked off) never logs a duplicate
    timing line for the same URL."""
    with scout_utils._timed(f"page fetch: {url}"):
        return await page_fetcher.fetch_page_text(url)


async def _fetch_text_cached(url: str, cache: _FetchCache | None) -> str | None:
    """page_fetcher.fetch_page_text with per-run dedup (see _FetchCache).
    cache=None (direct test calls) degrades to an uncached fetch."""
    if cache is None:
        return await _fetch_page_text_timed(url)
    future = cache.get(url)
    if future is None:
        future = asyncio.ensure_future(_fetch_page_text_timed(url))
        cache[url] = future
    return await future

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


async def _enrich_with_full_text(
    results: list[dict[str, Any]], cache: _FetchCache | None = None
) -> list[dict[str, Any]]:
    """Attaches the actual page text (not just title/snippet) for the top
    _RELATED_FETCH_LIMIT search results (deduped per run via `cache` - see
    _fetch_text_cached). Returns the same results with a "text" field added
    to each; a fetch failure just leaves it None for that result - never
    fatal. Both the fetched text and the existing snippet are passed through
    _redact_pii before being attached - this is the one place every Scout
    call site's search results flow through (blind search, representative
    search, targeted claim search, no-scrape link lookups), so redaction
    applies uniformly without duplicating the call at each site."""
    enriched = []
    for index, result in enumerate(results):
        text = await _fetch_text_cached(result["url"], cache) if index < _RELATED_FETCH_LIMIT else None
        enriched.append({**result, "snippet": scout_utils._redact_pii(result.get("snippet")), "text": scout_utils._redact_pii(text)})
    return enriched


async def _lookup_no_scrape_link(url: str, domain: str, company_name: str = "", cache: _FetchCache | None = None) -> dict[str, Any]:
    """Runs a targeted search for a login-walled platform link (LinkedIn,
    Twitter/X, etc.) instead of scraping it directly. Returns a dict with
    `full_profile_accessible` always False - the report must say this is a
    partial view, never the complete profile."""
    hits = await web_search_client.search(f"site:{domain} {url} overview", company_name=company_name)
    return {"url": url, "domain": domain, "results": await _enrich_with_full_text(hits, cache=cache), "full_profile_accessible": False}


async def _maybe_fetch_website(company_website: str | None) -> dict[str, Any] | None:
    """Fetches the company website (plus same-site subpages) if a URL was
    given. Returns None if no website was provided, or if the fetch failed."""
    if not company_website:
        return None
    return await page_fetcher.fetch_page_with_subpages(company_website)


async def _fill_blind_search(state: "_GatherState", company_name: str, cache: _FetchCache | None = None) -> None:
    """Pass A: industry-neutral background search, independent of the
    transcript. Each query runs as its own end-to-end pipeline (search ->
    relevance filter -> page-text enrichment -> write into `state`), all 8
    fully concurrent - searches AND enrichment fetches overlap, and each
    finished entry lands in `state.blind_search` immediately, so a gather
    deadline mid-run preserves every query that already completed instead of
    losing the lot. Each query's hits are passed through _filter_relevant
    before enrichment, to drop results that clearly aren't about
    `company_name` (a common company name can otherwise pull in an unrelated
    company's data). The revenue/employees/leadership queries
    (_RECENCY_BIASED_BLIND_SEARCH_QUERY_TEMPLATES) also pass
    settings.scout_recency_bias_time_range, biasing which results Tavily
    returns toward the past year - this does not attach a date label to any
    result, since Tavily doesn't provide one. Entries are re-sorted into
    template order at the end so downstream output stays deterministic."""

    async def _one_query(template: str) -> None:
        query = template.format(name=company_name)
        try:
            hits = await web_search_client.search(
                query,
                time_range=settings.scout_recency_bias_time_range
                if template in _RECENCY_BIASED_BLIND_SEARCH_QUERY_TEMPLATES
                else None,
                company_name=company_name,
            )
            relevant_hits = scout_utils._filter_relevant(hits, company_name)
            if not relevant_hits:
                return
            # Sorted by domain (alphabetical) for deterministic result order
            # across runs of the same query - selection/relevance filtering
            # above is unaffected, only the final presentation order.
            relevant_hits = sorted(relevant_hits, key=lambda hit: scout_utils._domain_of(hit["url"]) or "")
            entry = {"query": query, "results": await _enrich_with_full_text(relevant_hits, cache=cache)}
            state.blind_search.append(entry)
            state.already_fetched_urls.update(result["url"] for result in entry["results"])
        except Exception as exc:
            logger.warning("Blind search failed for query %r: %s", query, exc)

    await asyncio.gather(*(_one_query(template) for template in _BLIND_SEARCH_QUERY_TEMPLATES))
    template_order = [template.format(name=company_name) for template in _BLIND_SEARCH_QUERY_TEMPLATES]
    state.blind_search.sort(key=lambda entry: template_order.index(entry["query"]))


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
    company_tokens = scout_utils._normalize_company_tokens(company_name)
    mentioned = [
        url for url in scout_utils._extract_urls([extra_context, *(documents or [])])
        if not scout_utils._is_excluded_website_domain(scout_utils._domain_of(url), excluded_domains)
    ]
    if mentioned:
        # Mention order (not alphabetical - _extract_urls preserves it) with
        # the same PREFERRED-domain tiebreaker as the search fallback below:
        # a URL whose domain carries a company-name token beats an earlier-
        # mentioned third-party link (a press article, a partner page), which
        # would otherwise be crowned the "official" site and have its content
        # tagged source_tier="official".
        preferred = next(
            (url for url in mentioned if any(token in scout_utils._domain_of(url) for token in company_tokens)),
            None,
        )
        return preferred or mentioned[0]

    hits = await web_search_client.search(f"{company_name} official website", company_name=company_name)
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


async def _gather_relationship_search(company_name: str, cache: _FetchCache | None = None) -> list[dict[str, Any]]:
    """One dedicated search for any prior publicly known relationship,
    partnership, contract, or project between the company and the entity
    named by settings.scout_relationship_check_entity (a client-rubric
    requirement - "Saudi Aramco" by default, env-overridable, and disabled
    entirely when blank). Runs as its own named query rather than being
    folded into the generic _BLIND_SEARCH_QUERY_TEMPLATES loop (that set
    stays untouched). No recency bias is applied - unlike revenue/employees/
    leadership, an old partnership is still relevant however long ago it
    happened. Returns a single {"query", "results"} entry (same shape as a
    blind_search/representative_search entry) or [] if the check is
    disabled, nothing came back, or nothing survives relevance filtering -
    never raises, same as every other search in this module."""
    entity = settings.scout_relationship_check_entity.strip()
    if not entity:
        return []
    query = f"{company_name} {entity}"
    hits = await web_search_client.search(query, company_name=company_name)
    if not hits:
        return []
    relevant_hits = scout_utils._filter_relevant(hits, company_name)
    if not relevant_hits:
        return []
    # Sorted by domain (alphabetical) for deterministic result order across
    # runs of the same query - relevance filtering above is unaffected.
    relevant_hits = sorted(relevant_hits, key=lambda hit: scout_utils._domain_of(hit["url"]) or "")
    return [{"query": query, "results": await _enrich_with_full_text(relevant_hits, cache=cache)}]


async def _gather_representative_search(
    representative_name: str, company_name: str, cache: _FetchCache | None = None
) -> list[dict[str, Any]]:
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
    hits = await web_search_client.search(query, company_name=company_name)
    if not hits:
        return []
    relevant_hits = scout_utils._filter_relevant(hits, company_name, extra_name=representative_name)
    if not relevant_hits:
        return []
    # Sorted by domain (alphabetical) for deterministic result order across
    # runs of the same query - relevance filtering above is unaffected.
    relevant_hits = sorted(relevant_hits, key=lambda hit: scout_utils._domain_of(hit["url"]) or "")
    return [{"query": query, "results": await _enrich_with_full_text(relevant_hits, cache=cache)}]


async def _fill_link_lookups(
    state: "_GatherState", discovered_urls: list[str], company_name: str = "", cache: _FetchCache | None = None
) -> None:
    """No-scrape-domain targeted-search lookups for login-walled URLs
    discovered in extra_context, an attached document, or the website text
    (never the transcript). Matching is suffix-aware (regional/mobile
    subdomains like in.linkedin.com get the same search-instead-of-scrape
    treatment as the bare domain), the count is capped at _MAX_LINK_LOOKUPS
    (a footer link farm must not consume the whole gather budget - the drop
    is logged), and the lookups run concurrently, each writing into
    `state.link_lookups` as it completes so a deadline mid-run keeps the
    finished ones. Entries are re-sorted into discovery order at the end."""
    matching = [
        (url, domain)
        for url in discovered_urls
        if scout_utils._matches_domain(domain := scout_utils._domain_of(url), settings.no_scrape_domains)
    ]
    if len(matching) > _MAX_LINK_LOOKUPS:
        logger.info(
            "Capping no-scrape link lookups to %d (dropping %d of %d discovered).",
            _MAX_LINK_LOOKUPS, len(matching) - _MAX_LINK_LOOKUPS, len(matching),
        )
        matching = matching[:_MAX_LINK_LOOKUPS]

    async def _one_lookup(url: str, domain: str) -> None:
        try:
            lookup = await _lookup_no_scrape_link(url, domain, company_name, cache=cache)
            state.link_lookups.append(lookup)
            state.already_fetched_urls.add(url)
            state.already_fetched_urls.update(result["url"] for result in lookup["results"])
        except Exception as exc:
            logger.warning("Link lookup failed for %s: %s", url, exc)

    await asyncio.gather(*(_one_lookup(url, domain) for url, domain in matching))
    discovery_order = [url for url, _ in matching]
    state.link_lookups.sort(key=lambda lookup: discovery_order.index(lookup["url"]))


# Shared system prompt behind both _extract_claims (transcript + extra_context)
# and _extract_document_claims (documents only) - identical extraction rules;
# only the combined input text passed alongside this prompt differs between
# the two callers (see _run_claim_extraction_call).
_CLAIM_EXTRACTION_SYSTEM_PROMPT = (
    "Extract a JSON list of EVERY factual statement the company "
    "representative made, drawn from the labeled sections below "
    "(an interview transcript, additional free-text context they "
    "provided, and/or attached documents - not every section will "
    "be present). Treat claims from every section as equally "
    "weighted candidate claims about the company. A factual "
    "statement is anything asserted as true about the company, "
    "whether specific and checkable (a number, date, name, "
    "product or service, client, office location, milestone, "
    "industry ranking, award, certification, or partnership) or "
    "general (a scale/size assertion like 'thousands of "
    "deployments' or 'millions of users', a capability claim like "
    "'global presence' or 'Fortune 500 clients', a reputation "
    "claim like 'industry leading' or 'rapid growth', or an "
    "internal metric like '95% retention rate'). A ranking or "
    "award claim (e.g. 'we were ranked #1 by Gartner in 2025') IS "
    "a checkable factual claim, not an opinion - include it. The "
    "ONLY things to skip are pure opinions with no checkable "
    "content (e.g. 'we think collaboration matters') and vague, "
    "contentless intentions (e.g. 'we're planning to expand') - "
    "every other factual assertion about the company gets "
    "extracted, no matter how general or vague it sounds. A "
    "roadmap or development statement that names specific "
    "capabilities, products, or technologies being built (e.g. "
    "'we are developing AI agents, governance capabilities, and "
    "industry-specific AI solutions') is NOT a vague plan to "
    "skip - extract it as a \"specific\" claim with a search "
    "query, naming the capabilities mentioned. A partnership-"
    "model preference (e.g. 'we prefer co-innovation "
    "partnerships') must ALWAYS be extracted, however it is "
    "phrased - never skip a partnership-model statement as a "
    "mere opinion, since it is a key evaluation criterion. "
    "Extract claims EXACTLY as the vendor stated them - do "
    "not split, do not merge, do not rephrase. If the "
    "vendor said 'we serve banking, insurance, healthcare, "
    "government, and manufacturing' - extract that as ONE "
    "claim exactly as stated. If the vendor said 'we offer "
    "watsonx, Red Hat OpenShift, and IBM Cloud' - extract "
    "that as ONE claim. If the vendor said 'we are "
    "developing AI copilots and agentic automation' - "
    "extract that as ONE claim. The only transformation "
    "allowed is minor grammatical cleanup to make the claim "
    "a clear statement - never change the scope, subject, "
    "or meaning of what the vendor said. Extract claims about "
    "ANY factual topic - products "
    "or services, workforce, clients, revenue, offices, "
    "ownership, milestones, rankings, awards, certifications, "
    "partnerships, scale, reach, reputation, etc. - not only "
    "technical topics; this company may not be a technology "
    "company at all. For each claim, tag it with a \"category\" "
    "of exactly \"specific\" (it names a verifiable number, date, "
    "name, or entity) or \"general\" (a scale, capability, "
    "reputation, or internal-metric assertion with no such "
    "specific anchor). For a \"specific\" claim, also produce a "
    "short web-search phrase (3 to 5 words) that would help "
    "verify it; for a \"general\" claim, set \"search_query\" to "
    "null - it will be checked against already-gathered company "
    "data instead of a new search. Also tag which section each "
    "claim came from. Also tag each claim with its \"position\": "
    "an integer starting at 0, giving the exact order the claims "
    "were stated across the sections (top to bottom, transcript "
    "then context then documents) - number them sequentially in "
    "the order you encounter them as you read, never reordered by "
    "topic or importance; this position is what the final report "
    "uses to preserve interview order. Respond with STRICTLY a "
    "single JSON object (no prose, no markdown fences) of exactly "
    'this shape: {"claims": [{"claim": "<claim text>", "category": '
    '"specific"|"general", "search_query": "<3-5 word search '
    'phrase or null>", "source": "<transcript|context|document>", '
    '"position": <int>}, ...]}. If there are no extractable '
    'factual claims, return {"claims": []}.'
)


async def _run_claim_extraction_call(combined: str) -> list[dict[str, Any]]:
    """Shared Gemini call + parsing behind both _extract_claims and
    _extract_document_claims - same prompt/schema/timeout/retry behavior;
    only `combined` (the already-built, labeled input text) differs. Returns
    [] on any error or when there's no API key/content to extract from
    (soft-fails so a claim-extraction failure can never abort the whole
    run)."""
    if not settings.gemini_api_key or not combined.strip():
        return []

    payload = {
        "model": settings.scout_gemini_model,
        "messages": [
            {"role": "system", "content": _CLAIM_EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": combined},
        ],
        "temperature": 0,
    }

    try:
        # Through the shared gemini_client so the pinned-fallback-model retry
        # applies here too - an alias hot-swap must not silently disable
        # claim extraction.
        response_body = await gemini_client.chat_completion(
            payload,
            timeout=_CLAIM_EXTRACTION_TIMEOUT_SECONDS,
            fallback_model=settings.gemini_model_fallback,
        )
        content = response_body["choices"][0]["message"]["content"]
        claims = scout_utils._parse_claims(content)
        logger.info("Extracted %d claim(s)", len(claims))
        return claims
    except Exception as exc:
        logger.warning("Claim extraction failed: %s", exc)
        return []


async def _extract_claims(transcript: str | None, extra_context: str | None) -> list[dict[str, Any]]:
    """Pass B, step 1a: one Gemini call that pulls concrete, checkable
    factual claims out of the transcript and the intake form's free-text
    "anything else" field - both are candidate-provided statements about the
    company that should be checked against public data, treated as equally-
    weighted inputs. Skips opinions, plans, and vague/unfalsifiable
    statements. Covers ANY factual topic (products/services, workforce,
    clients, revenue, offices, ownership, milestones), not just technical
    ones, so this works for any industry. Returns the extracted claims (each
    with its own short web-search query and source label), or [] on any
    error or when neither input was provided.

    Deliberately does NOT include documents - those are extracted separately
    by _extract_document_claims, so document-derived claims get their own
    "Document Claims" report section and their own targeted search, kept
    entirely apart from transcript/extra_context claims (see
    scout_system_prompt's DOCUMENT CONTRADICTION RULE and "## Document
    Claims" section)."""
    combined = scout_utils._combine_claim_sources(transcript, extra_context, None)
    return await _run_claim_extraction_call(combined)


def _redact_documents(documents: list[str] | None) -> list[str]:
    """Redacts PII from every non-blank attached document's text - this is
    the one candidate-provided input that previously reached a Gemini call
    (claim extraction) without ever going through _redact_pii (transcript/
    extra_context aren't redacted either, but documents are singled out
    here because they also flow into the synthesis payload verbatim for the
    DOCUMENT CONTRADICTION RULE check - see run_scout's `sources["documents"]`).
    Blank/None entries are dropped entirely rather than redacted to empty
    strings."""
    return [scout_utils._redact_pii(doc) for doc in (documents or []) if doc and doc.strip()]


async def _extract_document_claims(documents: list[str] | None) -> list[dict[str, Any]]:
    """Pass B, step 1b: the document-only counterpart to _extract_claims -
    same extraction prompt/schema (via _run_claim_extraction_call), but the
    combined input text is built from ONLY the attached documents (never the
    transcript or extra_context), so a document-stated fact is extracted and
    later verified entirely independently of anything the vendor said in
    the interview. Document text is redacted via _redact_documents BEFORE
    being combined and sent to Gemini - this fixes the previously-known gap
    where document text was the one candidate-provided input that never
    passed through PII redaction on its way into a Gemini call. Returns []
    if there are no non-blank documents."""
    redacted_documents = _redact_documents(documents)
    if not redacted_documents:
        return []
    combined = scout_utils._combine_claim_sources(None, None, redacted_documents)
    return await _run_claim_extraction_call(combined)


async def _gather_targeted(
    claims: list[dict[str, Any]],
    already_fetched_urls: set[str],
    company_name: str,
    cache: _FetchCache | None = None,
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
        hits = await web_search_client.search(claim_obj["search_query"][:_MAX_CLAIM_QUERY_CHARS], company_name=company_name)
        selected_hits = scout_utils._filter_relevant(hits, company_name)[:_TARGETED_SEARCH_MAX_RESULTS]
        # Sorted by domain (alphabetical) for deterministic result order
        # across runs of the same query - which hits get selected above
        # (relevance-ranked, capped) is unaffected, only their final order.
        return sorted(selected_hits, key=lambda hit: scout_utils._domain_of(hit["url"]) or "")

    raw_results = await asyncio.gather(*(_search_claim(claim_obj) for claim_obj in selected), return_exceptions=True)

    # Dedup sequentially (cheap, no awaits - keeps deterministic first-claim-
    # wins semantics), then enrich all claims' fresh hits concurrently.
    fresh_lists: list[list[dict[str, Any]]] = []
    for claim_obj, hits in zip(selected, raw_results):
        if isinstance(hits, BaseException):
            logger.warning("Targeted search failed for claim %r: %s", claim_obj["claim"], hits)
            hits = []
        fresh_hits = [hit for hit in hits if hit["url"] not in already_fetched_urls]
        already_fetched_urls.update(hit["url"] for hit in fresh_hits)
        fresh_lists.append(fresh_hits)

    enriched_lists = await asyncio.gather(
        *(_enrich_with_full_text(fresh, cache=cache) for fresh in fresh_lists), return_exceptions=True
    )
    targeted: list[dict[str, Any]] = []
    for claim_obj, enriched in zip(selected, enriched_lists):
        if isinstance(enriched, BaseException):
            logger.warning("Targeted enrichment failed for claim %r: %s", claim_obj["claim"], enriched)
            enriched = []
        targeted.append({"claim": claim_obj["claim"], "results": enriched})
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
        self.relationship_search: list[dict[str, Any]] = []
        self.link_lookups: list[dict[str, Any]] = []
        self.claims: list[str] = []
        # The full extracted claim objects ({"claim", "category", "source",
        # "search_query"}) - the synthesis payload carries claim/category/
        # source so the model doesn't have to re-derive SPECIFIC vs GENERAL
        # from targeted_search presence (a proxy that breaks whenever Pass B
        # search is skipped, capped, or deduped to zero results).
        self.claim_details: list[dict[str, Any]] = []
        self.targeted_search: list[dict[str, Any]] = []
        # Document claims/search are kept entirely separate from the above -
        # extracted only from `documents` (never transcript/extra_context),
        # verified via their own dedicated targeted search, and rendered in
        # their own "## Document Claims" report section (see
        # scout_gather._extract_document_claims and
        # data_scout_agent.run_scout's `sources["document_claims"]`).
        self.document_claims: list[str] = []
        self.document_claim_details: list[dict[str, Any]] = []
        self.document_targeted_search: list[dict[str, Any]] = []
        # Redacted full document texts, carried alongside document_claims so
        # the synthesis prompt can apply the DOCUMENT CONTRADICTION RULE to
        # Interview Claims (checking a transcript claim against document
        # text directly, not just against document-derived claims).
        self.document_texts: list[str] = []
        self.already_fetched_urls: set[str] = set()


async def _run_pass_a(
    state: _GatherState,
    company_name: str,
    representative_name: str,
    extra_context: str | None,
    documents: list[str] | None,
    cache: _FetchCache | None = None,
) -> None:
    """Pass A: blind gathering, transcript NOT used for any search/fetch
    decision here. There is no dedicated website field on the intake form -
    the company website is derived first (see _derive_company_website), then
    its fetch, the 8 blind-search queries, the representative search, and
    the dedicated relationship-entity search (see
    _gather_relationship_search - client-rubric-required, configurable, no
    recency bias) all run concurrently - they're independent of each other.

    Each source writes into `state` at its OWN completion (not after a joint
    barrier) and swallows its own exceptions (logged) - so both a gather
    deadline firing mid-run and one source blowing up preserve everything
    the other sources already finished, honoring _GatherState's
    partial-progress contract for timeouts AND exceptions alike."""
    with scout_utils._timed("website derivation search"):
        company_website = await _derive_company_website(company_name, extra_context, documents)
    state.company_website = company_website

    async def _fill_website() -> None:
        page_data = await _maybe_fetch_website(company_website)
        if not page_data:
            return
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

    async def _fill_representative() -> None:
        entries = await _gather_representative_search(representative_name, company_name, cache=cache)
        state.representative_search = entries
        for entry in entries:
            state.already_fetched_urls.update(result["url"] for result in entry["results"])

    async def _fill_relationship() -> None:
        entries = await _gather_relationship_search(company_name, cache=cache)
        state.relationship_search = entries
        for entry in entries:
            state.already_fetched_urls.update(result["url"] for result in entry["results"])

    async def _guarded(name: str, coro: Any) -> None:
        with scout_utils._timed(name):
            try:
                await coro
            except Exception as exc:
                logger.warning("Pass A %s failed: %s: %s", name, type(exc).__name__, exc)

    await asyncio.gather(
        _guarded("website fetch", _fill_website()),
        _guarded("blind search", _fill_blind_search(state, company_name, cache=cache)),
        _guarded("representative search", _fill_representative()),
        _guarded("relationship search", _fill_relationship()),
    )

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

    await _guarded("link lookups", _fill_link_lookups(state, discovered_urls, company_name, cache=cache))


async def _extract_claims_step(
    transcript: str | None, extra_context: str | None
) -> list[dict[str, Any]]:
    """Pass B, step 1a (interview claim extraction) - split out of the old
    _run_pass_b so _gather_all can run this CONCURRENTLY with Pass A (see
    _gather_all): extraction is a Gemini call over the transcript/
    extra_context alone - neither of which come from Pass A - so serializing
    it strictly after Pass A (the pre-2026-08-01 behavior) wasted whichever
    of the two finished first entirely off the critical path. Only the
    targeted-search step below genuinely needs Pass A's already_fetched_urls
    (for dedup), so that part still waits for Pass A to finish. Runs only if
    at least one of the two candidate inputs was actually provided; returns
    [] otherwise. Documents are handled entirely separately by
    _extract_document_claims_step - see that function's docstring."""
    has_input = (transcript and transcript.strip()) or (extra_context and extra_context.strip())
    if not has_input:
        return []
    with scout_utils._timed("claim extraction (Gemini)"):
        return await _extract_claims(transcript, extra_context)


async def _extract_document_claims_step(documents: list[str] | None) -> list[dict[str, Any]]:
    """Pass B, step 1b (document claim extraction) - the document-only
    counterpart to _extract_claims_step, run CONCURRENTLY alongside it and
    Pass A in _gather_all. Kept as its own step (rather than folded into
    _extract_claims_step) so document-derived claims never mix with
    transcript/extra_context claims: they get their own extraction call,
    their own targeted search, and their own "## Document Claims" report
    section (see scout_system_prompt). Runs only if at least one non-blank
    document was provided; returns [] otherwise."""
    has_input = any(doc and doc.strip() for doc in (documents or []))
    if not has_input:
        return []
    with scout_utils._timed("document claim extraction (Gemini)"):
        return await _extract_document_claims(documents)


async def _gather_all(
    state: _GatherState,
    company_name: str,
    representative_name: str,
    extra_context: str | None,
    documents: list[str] | None,
    transcript: str | None,
) -> None:
    """Runs Pass A and Pass B's claim extraction CONCURRENTLY - measured
    2026-08-01: claim extraction has no actual dependency on Pass A's
    output, so running it after Pass A finished (the old sequential design)
    cost the extraction call's full duration (~8s in a representative run)
    for nothing. Only Pass B's SECOND step (the per-claim targeted search)
    genuinely needs Pass A's already_fetched_urls (for dedup), so that part
    still runs strictly after both of the above finish. One page-text fetch
    cache spans the whole run so the same URL is never fetched and parsed
    twice.

    Document claim extraction (_extract_document_claims_step) runs
    alongside Pass A and interview claim extraction in the same gather -
    same rationale (no dependency on either), and it keeps document-derived
    claims entirely separate from transcript/extra_context claims from the
    moment they're extracted (see scout_gather._extract_document_claims).

    return_exceptions=True (matching every other concurrent gather in this
    module - see _guarded, _gather_targeted): Pass A already swallows its
    own per-source failures internally, but the one call inside it that
    isn't individually guarded (_derive_company_website) can still raise:
    if it does, the exception is logged here rather than also discarding
    the two claim-extraction steps' independently-obtained results,
    consistent with _GatherState's partial-progress philosophy (one piece
    failing must not lose what another, unrelated piece already produced)."""
    cache: _FetchCache = {}
    pass_a_result, extracted_result, document_extracted_result = await asyncio.gather(
        _run_pass_a(state, company_name, representative_name, extra_context, documents, cache=cache),
        _extract_claims_step(transcript, extra_context),
        _extract_document_claims_step(documents),
        return_exceptions=True,
    )
    if isinstance(pass_a_result, BaseException):
        logger.warning("Pass A failed: %s: %s", type(pass_a_result).__name__, pass_a_result)
    if isinstance(extracted_result, BaseException):
        logger.warning("Claim extraction step failed: %s: %s", type(extracted_result).__name__, extracted_result)
        extracted_result = []
    if isinstance(document_extracted_result, BaseException):
        logger.warning(
            "Document claim extraction step failed: %s: %s",
            type(document_extracted_result).__name__, document_extracted_result,
        )
        document_extracted_result = []

    state.claims = [claim_obj["claim"] for claim_obj in extracted_result]
    state.claim_details = extracted_result
    state.document_claims = [claim_obj["claim"] for claim_obj in document_extracted_result]
    state.document_claim_details = document_extracted_result
    state.document_texts = _redact_documents(documents)

    if extracted_result:
        with scout_utils._timed("targeted per-claim search"):
            state.targeted_search = await _gather_targeted(
                extracted_result, state.already_fetched_urls, company_name, cache=cache
            )
    if document_extracted_result:
        # Runs sequentially after the interview targeted search above (not
        # concurrently with it) so both can safely dedup against the same
        # state.already_fetched_urls set without racing on its mutation -
        # still entirely its own dedicated search per document claim,
        # completely separate from targeted_search/interview claims.
        with scout_utils._timed("document targeted per-claim search"):
            state.document_targeted_search = await _gather_targeted(
                document_extracted_result, state.already_fetched_urls, company_name, cache=cache
            )
