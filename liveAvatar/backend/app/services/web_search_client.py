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


def _source_tier(url: str) -> str:
    """Classifies a result's domain into a source_tier for Scout's synthesis
    step to weigh by credibility: "trusted" for a domain in
    settings.scout_trusted_domains (recognized, reputable sources), else
    "unverified". Never raises - any error while classifying (e.g. an
    unparsable URL) defaults to "unverified" rather than dropping the result
    or crashing the whole search call."""
    try:
        # Suffix-aware like every other domain blocklist in the scout
        # pipeline - "news.reuters.com" must count as "reuters.com".
        return (
            "trusted"
            if scout_utils._matches_domain(_domain_of(url), settings.scout_trusted_domains)
            else "unverified"
        )
    except Exception:
        logger.warning("Source-tier classification failed for %r; defaulting to unverified.", url, exc_info=True)
        return "unverified"


def _extract_results(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Adapts Tavily's raw response shape into this app's internal
    {"title", "url", "snippet", "source_tier"} shape - if Tavily's shape ever
    changes, only this function needs to change. Filters out any result
    whose domain is in settings.scout_low_quality_domains (known content
    farms/spam aggregators) so every Scout call site benefits uniformly
    without duplicating the check; a classification failure here keeps the
    result (tagged "unverified") rather than dropping it or raising. Returns
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
                "source_tier": _source_tier(url),
            }
        )
    return results


async def search(query: str, *, time_range: str | None = None) -> list[dict[str, Any]]:
    """Runs a web search via Tavily's Search API. Returns
    [{"title", "url", "snippet"}, ...]; empty list means "not available",
    never raises, so a single failed query never aborts the scout pipeline
    (app.services.data_scout_agent treats [] as "not available").

    `time_range` is passed through to Tavily's request payload verbatim
    when given (e.g. "y" for the past year) to bias which results come
    back toward more recent ones - omitting it (the default) leaves the
    request exactly as before this parameter existed."""
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
        return _extract_results(response_body)
    except Exception as exc:
        logger.warning("Tavily search failed for query %r: %s: %s", query, type(exc).__name__, exc)
        return []
