"""Pure helper functions for the Data Scout Agent: no async, no network I/O.
Domain/URL parsing, PII redaction, same-company relevance filtering, source
truncation, and claim-text parsing - all deterministic, synchronous, and
side-effect-free (config reads aside).
"""

import re
from typing import Any
from urllib.parse import urlparse

from app.config import settings
from app.services.llm_json import parse_llm_json

# --- Payload truncation (see _truncate_sources / _combine_claim_sources) ---
# Per-field character caps applied before the sources dict is serialised and
# sent to Gemini. The gathered page/search text is the primary cause of
# ReadTimeout: a single company page alone can be 30 000+ chars, and with
# three subpages plus enriched search results the JSON payload can easily
# exceed 200 000 chars - Gemini Flash needs 60-120 s to process that even
# before it starts generating, which blows through any reasonable httpx
# timeout. These caps keep the total payload manageable while preserving the
# most informative content (the opening of each text block).
_MAX_PAGE_TEXT_CHARS = 3_000
_MAX_SUBPAGE_TEXT_CHARS = 1_500
_MAX_SEARCH_RESULT_CHARS = 1_000
_MAX_TRANSCRIPT_CHARS = 8_000  # transcript text passed to the (Pass B only) claim-extraction call
_MAX_EXTRA_CONTEXT_CHARS = 4_000  # intake "anything else about you or your company" text, same call
_MAX_DOCUMENT_CHARS = 4_000  # each attached document's extracted text, same call

_URL_RE = re.compile(r"https?://\S+")  # matches a bare URL in free text (transcript or fetched page text)

# --- Claim-selection heuristic (see _claim_specificity_score / _select_targeted_claims) ---
_MAX_TARGETED_CLAIMS = 10  # Pass B is capped at this many targeted searches regardless of how many claims were extracted
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
# At least one real separator (dash/dot/space/parens) is REQUIRED between the
# groups: with every separator optional this also matched any bare 10-digit
# integer, redacting revenue figures like "4500000000" out of fetched pages -
# exactly the facts the revenue/employees blind searches exist to find. A
# separator-less phone number now slips through unredacted; that trade beats
# destroying financial data on every ordinary run.
_PHONE_RE = re.compile(
    # (?<!\w) not \b: a leading "(" has no word boundary after a space, so
    # \b silently never matched parenthesized numbers like "(555) 123-4567".
    r"(?<!\w)(?:\+?\d{1,3}[-.\s])?(?:\(\d{3}\)[-.\s]?|\d{3}[-.\s])\d{3}[-.\s]\d{4}\b"
)
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
# Case-SENSITIVE with capitalized filler words required: the previous
# lowercase-tolerant filler class matched ordinary prose ending in an
# abbreviation - "150 employees led by Dr. Ahmed" redacted the headcount AND
# the CEO's title on every ordinary run. Real street lines ("123 Main
# Street", "1 Infinite Loop Suite 4") capitalize their words.
_ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][A-Za-z0-9.]*\s+){1,4}"
    r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Lane|Ln|Way|Court|Ct|Place|Pl|Suite|Ste)\.?\b"
)

# --- Same-company relevance filter (see _filter_relevant / _is_relevant_result) ---
# Cheap heuristic, no LLM call: tolerates common company-suffix/punctuation
# variance rather than requiring an exact name match. Known gap - cannot
# distinguish two different companies that share the same/similar name;
# only catches results about something with a clearly different name
# entirely (deliberately lenient, biased toward keeping borderline results).
_COMPANY_SUFFIX_RE = re.compile(r"\b(?:inc|llc|ltd|limited|corp|corporation|company|co|group|plc|gmbh)\b\.?", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")


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
    """Finds every URL mentioned across the given text blobs (intake text,
    fetched page/subpage text). Returns a deduplicated list in MENTION order
    (first occurrence wins) - _derive_company_website treats the first
    intake-mentioned URL as the likeliest company site, and the previous
    sorted() return quietly made that "alphabetically first" instead (a
    medium.com press link would beat the company's own zenithcorp.com)."""
    urls: list[str] = []
    seen: set[str] = set()
    for blob in text_blobs:
        if not blob:
            continue
        for match in _URL_RE.findall(blob):
            url = match.rstrip(").,;\"'")
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


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

    if "relationship_search" in sources:
        # Same {"query", "results"} shape as blind_search/representative_search -
        # one dedicated search for a prior relationship with the configured
        # entity (settings.scout_relationship_check_entity, a client-rubric
        # requirement - see scout_relationship_check_prompt's mandatory
        # Additional Findings bullet).
        truncated["relationship_search"] = [
            {
                **entry,
                "results": [
                    {**result, "text": _trunc(result.get("text"), _MAX_SEARCH_RESULT_CHARS)}
                    for result in (entry.get("results") or [])
                ],
            }
            for entry in sources["relationship_search"]
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


def _matches_domain(domain: str, candidates: "set[str] | tuple[str, ...]") -> bool:
    """True if `domain` is one of `candidates` or a subdomain of one (e.g.
    "in.linkedin.com" matches "linkedin.com"). The one domain-matching rule
    shared by every blocklist check in the scout pipeline - exact matching
    alone let regional/mobile subdomains slip through."""
    return any(
        domain == candidate or domain.endswith(f".{candidate}")
        for candidate in candidates
    )


def _is_excluded_website_domain(domain: str, excluded_domains: set[str]) -> bool:
    """True if `domain` matches (exactly or as a subdomain - see
    _matches_domain) `excluded_domains` or
    settings.scout_reference_site_domains - the latter are third-party
    reference/aggregator sites that are scrapeable but are never a company's
    own official site, unlike the login-walled/scraper-blocking domains in
    `excluded_domains` itself."""
    return _matches_domain(domain, excluded_domains) or _matches_domain(
        domain, settings.scout_reference_site_domains
    )


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
    expected to be {"claim", "category", "search_query", "source"}; a bare
    string is tolerated too (category defaults to "specific", search_query
    falls back to the claim text itself, source to "transcript") in case the
    model ignores the object shape. A "general" claim (scale/capability/
    reputation/internal-metric assertion, no specific number/date/name/
    entity attached) keeps search_query as None rather than defaulting it to
    the claim text - see _select_targeted_claims, which relies on that None
    to keep general claims out of Pass B search entirely; they're judged in
    synthesis against already-gathered Pass A data instead. Returns the
    non-empty, stripped claim objects.

    Parsing goes through the shared llm_json.parse_llm_json (the helper every
    other agent uses) - it tolerates leading prose, trailing text, and
    language-tagged fences that the previous hand-rolled fence-stripping
    choked on, each of which silently emptied the Interview Claims section."""
    parsed = parse_llm_json(content)
    raw_claims = parsed.get("claims", [])

    claims: list[dict[str, Any]] = []
    for item in raw_claims:
        if isinstance(item, dict):
            claim_text = item.get("claim")
            if not isinstance(claim_text, str) or not claim_text.strip():
                continue
            category = item.get("category")
            category = "general" if isinstance(category, str) and category.strip().lower() == "general" else "specific"
            search_query = item.get("search_query")
            search_query = search_query.strip() if isinstance(search_query, str) and search_query.strip() else None
            if search_query is None and category != "general":
                search_query = claim_text.strip()
            source = item.get("source")
            source = source.strip() if isinstance(source, str) and source.strip() else "transcript"
            claims.append({"claim": claim_text.strip(), "category": category, "search_query": search_query, "source": source})
        elif isinstance(item, str) and item.strip():
            claims.append({"claim": item.strip(), "category": "specific", "search_query": item.strip(), "source": "transcript"})
    return claims


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
    claim's text). "General" claims (search_query is None - see
    _parse_claims) are filtered out before the cap is even considered, so
    they never consume a search slot regardless of how many total claims
    were extracted; they still reach synthesis via state.claims independently
    of this function, judged against Pass A data instead. Returns the
    selected claim objects in their original extraction order."""
    searchable = [claim for claim in claims if claim.get("search_query")]
    if len(searchable) <= _MAX_TARGETED_CLAIMS:
        return searchable
    ranked = sorted(enumerate(searchable), key=lambda pair: (-_claim_specificity_score(pair[1]["claim"]), pair[0]))
    selected_indices = sorted(index for index, _ in ranked[:_MAX_TARGETED_CLAIMS])
    return [searchable[index] for index in selected_indices]
