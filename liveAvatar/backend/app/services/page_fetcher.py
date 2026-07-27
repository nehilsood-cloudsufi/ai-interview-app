import logging
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# --- Fetch tuning ---
_PAGE_FETCH_TIMEOUT_SECONDS = 10.0  # per-request httpx timeout for page/subpage fetches
_MAX_CHARS = 12000  # cap on extracted page text length, keeps downstream synthesis payloads bounded
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DataScoutAgent/1.0)"}  # identifies this fetcher to servers that block unlabeled/bot requests

# --- Same-site subpage discovery ---
# A thin homepage often hides the actual substance on /about, /projects, etc.
# Kept to a small fixed depth-1 crawl (see _MAX_SUBPAGES) so this can't
# runaway into crawling an entire site.
_SUBPAGE_KEYWORDS = ("about", "project", "portfolio", "resume", "cv", "experience", "work")  # path/anchor-text keywords worth following
_MAX_SUBPAGES = 3  # max same-domain subpages fetched per page


def _extract_subpage_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Finds up to _MAX_SUBPAGES same-domain links on the page whose path or
    anchor text matches _SUBPAGE_KEYWORDS. Returns the absolute URLs."""
    base_domain = urlparse(base_url).netloc.lower()
    base_normalized = base_url.split("#")[0].rstrip("/")
    seen: set[str] = set()
    links: list[str] = []

    for anchor in soup.find_all("a", href=True):
        absolute = urljoin(base_url, anchor["href"])
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or parsed.netloc.lower() != base_domain:
            continue

        haystack = f"{parsed.path.lower()} {anchor.get_text(strip=True).lower()}"
        if not any(keyword in haystack for keyword in _SUBPAGE_KEYWORDS):
            continue

        normalized = absolute.split("#")[0].rstrip("/")
        if normalized in seen or normalized == base_normalized:
            continue
        seen.add(normalized)
        links.append(absolute)
        if len(links) >= _MAX_SUBPAGES:
            break

    return links


async def _fetch_one(client: httpx.AsyncClient, url: str, discover_links: bool) -> tuple[str | None, list[str]]:
    """Fetches a single URL and extracts its visible text (capped at
    _MAX_CHARS), optionally also discovering same-site subpage links. Returns
    (text, subpage_links); text is None (logged, non-fatal) on any fetch
    failure or non-HTML response."""
    try:
        response = await client.get(url, headers=_HEADERS)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch page %s: %s", url, exc)
        return None, []

    content_type = response.headers.get("content-type", "")
    if "html" not in content_type:
        logger.info("Skipping non-HTML content at %s (content-type: %s)", url, content_type)
        return None, []

    soup = BeautifulSoup(response.text, "html.parser")
    # Link discovery must run before nav/footer/header are stripped below -
    # that's exactly where "About"/"Projects" links usually live.
    subpage_links = _extract_subpage_links(soup, url) if discover_links else []

    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    # Prefer the page's main content area over full-document text (which
    # otherwise pulls in sidebar/boilerplate chrome and drowns out the
    # actual content) - falls back to the whole document if neither exists.
    content = soup.find("main") or soup.find("article") or soup
    text = " ".join(content.get_text(separator=" ").split())
    return (text[:_MAX_CHARS] if text else None), subpage_links


async def fetch_page_text(url: str) -> str | None:
    """Fetches and extracts visible text from a single generic page (personal
    site, blog, Medium, dev.to, Kaggle, Behance, etc.). Returns None (logged,
    non-fatal) on any failure so the scout pipeline can degrade gracefully."""
    async with httpx.AsyncClient(timeout=_PAGE_FETCH_TIMEOUT_SECONDS, follow_redirects=True) as client:
        text, _ = await _fetch_one(client, url, discover_links=False)
    return text


async def fetch_page_with_subpages(url: str) -> dict[str, Any] | None:
    """Fetches a generic page plus up to _MAX_SUBPAGES same-domain sub-pages
    it links to (About/Projects/Resume/etc.) - a thin homepage often hides
    the actual substance one click away. Depth is fixed at 1 (sub-pages are
    never themselves crawled for further links). Returns None only if the
    root page itself couldn't be fetched; a sub-page failing just means it's
    omitted, same graceful-degradation contract as fetch_page_text. Returns
    {"text": str, "subpages": [{"url": str, "text": str}, ...]}."""
    async with httpx.AsyncClient(timeout=_PAGE_FETCH_TIMEOUT_SECONDS, follow_redirects=True) as client:
        text, subpage_links = await _fetch_one(client, url, discover_links=True)
        if text is None:
            return None

        subpages = []
        for link in subpage_links:
            sub_text, _ = await _fetch_one(client, link, discover_links=False)
            if sub_text:
                subpages.append({"url": link, "text": sub_text})

        return {"text": text, "subpages": subpages}
