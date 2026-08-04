import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.services import scout_utils

logger = logging.getLogger(__name__)

_SEARCH_TIMEOUT_SECONDS = 30.0  # per-request httpx timeout for Tavily search calls
_MAX_RESULTS = 5  # Tavily results requested per query, shared by every caller (Pass A and Pass B alike)


def _domain_of(url: str) -> str:
    """Returns the lowercase registrable domain of a URL, stripping a
    leading "www." - a local copy of data_scout_agent's identical helper
    (importing it from there would be circular, since data_scout_agent
    imports this module)."""
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


# Tier 1 press-release wire domains (part of the two-tier source_tier
# system - see _source_tier). Suffix-aware matched like every other domain
# list in this module.
_PRESS_RELEASE_DOMAINS = ("prnewswire.com", "businesswire.com", "globenewswire.com", "accesswire.com")


def _is_official_linkedin_company_page(url: str, company_tokens: set[str]) -> bool:
    """Tier 1 rule 2: the company's own LinkedIn company page - domain is
    linkedin.com, path starts with /company/, and a company-name token
    appears in the path (e.g. /company/cloudsufi). A LinkedIn post, profile,
    or someone else's company page does not qualify."""
    path = urlparse(url).path.lower()
    return _domain_of(url) == "linkedin.com" and path.startswith("/company/") and any(token in path for token in company_tokens)


def _is_official_press_release(url: str, company_tokens: set[str]) -> bool:
    """Tier 1 rule 3: a company press release on one of the four wire
    services, with a company-name token appearing somewhere in the URL
    path (a wire service hosts every company's releases, so the domain
    alone isn't enough to confirm it's THIS company's own release)."""
    path = urlparse(url).path.lower()
    return scout_utils._matches_domain(_domain_of(url), _PRESS_RELEASE_DOMAINS) and any(token in path for token in company_tokens)


def _source_tier(url: str, company_name: str = "") -> str:
    """Classifies a result's domain into a two-tier source_tier for Scout's
    synthesis step to weigh by credibility: "official" for exactly three
    Tier 1 categories - the company's own derived website (tagged directly
    in scout_gather.py's website-fetch path, not here), its official
    LinkedIn company page (_is_official_linkedin_company_page), or a
    company press release (_is_official_press_release) - "external" for
    everything else (government filings, news sites, directories,
    Wikipedia, SEC filings, social media, analyst reports, blogs, forums,
    review sites - no more separate "trusted" tier). Never raises - any
    error while classifying (e.g. an unparsable URL) defaults to "external"
    rather than dropping the result or crashing the whole search call."""
    try:
        company_tokens = scout_utils._normalize_company_tokens(company_name)
        if company_tokens and (
            _is_official_linkedin_company_page(url, company_tokens)
            or _is_official_press_release(url, company_tokens)
        ):
            return "official"
        return "external"
    except Exception:
        logger.warning("Source-tier classification failed for %r; defaulting to external.", url, exc_info=True)
        return "external"


def _extract_results(data: dict[str, Any], company_name: str = "") -> list[dict[str, Any]]:
    """Adapts Tavily's raw response shape into this app's internal
    {"title", "url", "snippet", "source_tier"} shape - if Tavily's shape ever
    changes, only this function needs to change. Filters out any result
    whose domain is in settings.scout_low_quality_domains (known content
    farms/spam aggregators) so every Scout call site benefits uniformly
    without duplicating the check; a classification failure here keeps the
    result (tagged "external") rather than dropping it or raising. Returns
    the mapped, filtered result list."""
    results = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not url:
            continue
        try:
            if scout_utils._matches_domain(_domain_of(url), settings.scout_low_quality_domains):
                continue
        except Exception:
            logger.warning("Low-quality domain check failed for %r; keeping result.", url, exc_info=True)
        results.append(
            {
                "title": item.get("title") or url,
                "url": url,
                "snippet": (item.get("content") or "").strip(),
                "source_tier": _source_tier(url, company_name),
            }
        )
    return results


async def search(query: str, *, time_range: str | None = None, company_name: str = "") -> list[dict[str, Any]]:
    """Runs a web search via Tavily's Search API. Returns
    [{"title", "url", "snippet"}, ...]; empty list means "not available",
    never raises, so a single failed query never aborts the scout pipeline
    (app.services.data_scout_agent treats [] as "not available").

    `time_range` is passed through to Tavily's request payload verbatim
    when given (e.g. "y" for the past year) to bias which results come
    back toward more recent ones - omitting it (the default) leaves the
    request exactly as before this parameter existed.

    `company_name` is forwarded to _extract_results/_source_tier so the
    LinkedIn-company-page and press-release Tier 1 checks can match
    against the company's own name tokens - omitting it degrades every
    result to "external" (no company to match against), never raises."""
    if not settings.tavily_api_key:
        logger.info("Web search not configured (TAVILY_API_KEY unset); skipping query: %s", query)
        return []

    payload: dict[str, Any] = {
        "query": query,
        "max_results": _MAX_RESULTS,
    }
    if time_range is not None:
        payload["time_range"] = time_range

    # Except-Exception, not just httpx.HTTPError: a 200 with a non-JSON body
    # (response.json() -> JSONDecodeError) or an unexpected response shape
    # must also degrade to "not available" - callers rely on the never-raises
    # contract, and an escape here would abort the whole scout pipeline.
    try:
        async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{settings.tavily_api_base_url}/search",
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.tavily_api_key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            response_body = response.json()
        return _extract_results(response_body, company_name)
    except Exception as exc:
        logger.warning("Tavily search failed for query %r: %s: %s", query, type(exc).__name__, exc)
        return []
