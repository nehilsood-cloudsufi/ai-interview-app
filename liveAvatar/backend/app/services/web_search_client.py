import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_SEARCH_TIMEOUT_SECONDS = 30.0  # per-request httpx timeout for Tavily search calls
_MAX_RESULTS = 5  # Tavily results requested per query, shared by every caller (Pass A and Pass B alike)


def _extract_results(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Adapts Tavily's raw response shape into this app's internal
    {"title", "url", "snippet"} shape - if Tavily's shape ever changes, only
    this function needs to change. Returns the mapped result list."""
    results = []
    for item in data.get("results") or []:
        url = item.get("url")
        if not url:
            continue
        results.append(
            {
                "title": item.get("title") or url,
                "url": url,
                "snippet": (item.get("content") or "").strip(),
            }
        )
    return results


async def search(query: str) -> list[dict[str, Any]]:
    """Runs a web search via Tavily's Search API. Returns
    [{"title", "url", "snippet"}, ...]; empty list means "not available",
    never raises, so a single failed query never aborts the scout pipeline
    (app.services.data_scout_agent treats [] as "not available")."""
    if not settings.tavily_api_key:
        logger.info("Web search not configured (TAVILY_API_KEY unset); skipping query: %s", query)
        return []

    payload = {
        "query": query,
        "max_results": _MAX_RESULTS,
    }

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
    except httpx.HTTPError as exc:
        logger.warning("Tavily search failed for query %r: %s", query, exc)
        return []

    return _extract_results(response_body)
