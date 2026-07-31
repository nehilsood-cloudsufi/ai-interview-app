import asyncio
import ipaddress
import logging
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# --- Fetch tuning ---
_PAGE_FETCH_TIMEOUT_SECONDS = 10.0  # per-request httpx timeout for page/subpage fetches
_MAX_CHARS = 12000  # cap on extracted page text length, keeps downstream synthesis payloads bounded
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DataScoutAgent/1.0)"}  # identifies this fetcher to servers that block unlabeled/bot requests

# --- SSRF guard (see _is_public_url / _get_validated) ---
# Every URL this module fetches is attacker-influencable: the company
# website is derived from vendor-typed intake text, and search-result URLs
# come from a third-party API. Each fetch (and each redirect hop - redirects
# are followed manually, not by httpx) must therefore resolve to a public
# address: http/https only, and every resolved IP must be global (blocking
# loopback, RFC1918/private, link-local incl. the 169.254.169.254 cloud
# metadata endpoint, and reserved ranges). DNS is resolved once for the
# check and again by httpx for the fetch - a rebinding race between the two
# is accepted for this POC. Response bodies are streamed with a byte cap so
# an attacker-hosted page can't balloon memory before the text extraction
# even starts.
_MAX_REDIRECTS = 5
_MAX_RESPONSE_BYTES = 2_000_000

# --- Same-site subpage discovery ---
# A thin homepage often hides the actual substance on /about, /projects, etc.
# Kept to a small fixed depth-1 crawl (see _MAX_SUBPAGES) so this can't
# runaway into crawling an entire site.
_SUBPAGE_KEYWORDS = ("about", "project", "portfolio", "resume", "cv", "experience", "work")  # path/anchor-text keywords worth following
_MAX_SUBPAGES = 3  # max same-domain subpages fetched per page


def _normalized_netloc(netloc: str) -> str:
    """Lowercased netloc with a leading "www." stripped, so a same-site
    comparison isn't defeated by a www-vs-bare mismatch (a bare-domain
    homepage linking to www. About/Projects pages, or vice versa)."""
    lowered = netloc.lower()
    return lowered[4:] if lowered.startswith("www.") else lowered


def _extract_subpage_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Finds up to _MAX_SUBPAGES same-domain links on the page whose path or
    anchor text matches _SUBPAGE_KEYWORDS. Returns the absolute URLs."""
    base_domain = _normalized_netloc(urlparse(base_url).netloc)
    base_normalized = base_url.split("#")[0].rstrip("/")
    seen: set[str] = set()
    links: list[str] = []

    for anchor in soup.find_all("a", href=True):
        absolute = urljoin(base_url, anchor["href"])
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or _normalized_netloc(parsed.netloc) != base_domain:
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


async def _is_public_url(url: str) -> bool:
    """SSRF check for one URL: http/https scheme, and the host (a literal IP
    or every DNS-resolved address) must be a public/global IP - loopback,
    private (RFC1918), link-local (incl. the cloud metadata endpoint), and
    reserved ranges are all rejected, as is anything that fails to
    resolve."""
    parsed = urlparse(url)
    host = parsed.hostname
    if parsed.scheme not in ("http", "https") or not host:
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        pass  # not an IP literal - resolve it below
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    addresses = {info[4][0] for info in infos}
    if not addresses:
        return False
    try:
        return all(ipaddress.ip_address(address).is_global for address in addresses)
    except ValueError:
        return False


async def _read_capped(response: httpx.Response) -> str:
    """Reads a streamed response body up to _MAX_RESPONSE_BYTES (truncating
    beyond that, logged) and decodes it with the response's declared charset
    (utf-8 fallback, undecodable bytes replaced)."""
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) >= _MAX_RESPONSE_BYTES:
            logger.info(
                "Page at %s exceeds %d bytes; truncating before parse.",
                response.request.url, _MAX_RESPONSE_BYTES,
            )
            break
    return bytes(body[:_MAX_RESPONSE_BYTES]).decode(response.encoding or "utf-8", errors="replace")


async def _get_validated(client: httpx.AsyncClient, url: str) -> tuple[httpx.Response, str] | None:
    """GETs a URL with the SSRF check applied to it AND to every redirect hop
    (redirects are followed manually here - the client must have
    follow_redirects disabled), streaming the winning response's body through
    the byte cap. Returns (response, body_text), or None when the URL (or a
    hop) is blocked or the redirect chain is too long. HTTP errors raise, as
    before - the caller catches them."""
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        if not await _is_public_url(current):
            logger.warning("Blocked page fetch of non-public/unresolvable URL %s", current)
            return None
        request = client.build_request("GET", current, headers=_HEADERS)
        response = await client.send(request, stream=True)
        try:
            if response.is_redirect and "location" in response.headers:
                current = urljoin(current, response.headers["location"])
                continue
            response.raise_for_status()
            return response, await _read_capped(response)
        finally:
            await response.aclose()
    logger.warning("Too many redirects (>%d) fetching %s", _MAX_REDIRECTS, url)
    return None


def _parse_page(body: str, url: str, discover_links: bool) -> tuple[str | None, list[str]]:
    """CPU-bound half of a fetch: BeautifulSoup parse, optional subpage-link
    discovery, chrome-stripping, and visible-text extraction (capped at
    _MAX_CHARS). Synchronous by design - callers run it via asyncio.to_thread
    so a multi-hundred-KB parse can't block the event loop that is also
    serving live interview turns for other sessions."""
    soup = BeautifulSoup(body, "html.parser")
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


async def _fetch_one(client: httpx.AsyncClient, url: str, discover_links: bool) -> tuple[str | None, list[str]]:
    """Fetches a single URL (SSRF-validated per hop, body size-capped - see
    _get_validated) and extracts its visible text (capped at _MAX_CHARS,
    parsed off the event loop - see _parse_page), optionally also discovering
    same-site subpage links. Returns (text, subpage_links); text is None
    (logged, non-fatal) on any fetch failure, blocked URL, or non-HTML
    response."""
    try:
        fetched = await _get_validated(client, url)
    except Exception as exc:
        # Broad on purpose: this helper's never-break-the-pipeline contract
        # must hold for more than httpx.HTTPError - e.g. httpx.InvalidURL
        # (malformed redirect Location, bad IDNA label) is NOT an HTTPError
        # subclass and used to escape all the way up and discard the entire
        # scout report.
        logger.warning("Failed to fetch page %s: %s: %s", url, type(exc).__name__, exc)
        return None, []
    if fetched is None:
        return None, []
    response, body = fetched

    content_type = response.headers.get("content-type", "")
    if "html" not in content_type:
        logger.info("Skipping non-HTML content at %s (content-type: %s)", url, content_type)
        return None, []

    return await asyncio.to_thread(_parse_page, body, url, discover_links)


async def fetch_page_text(url: str) -> str | None:
    """Fetches and extracts visible text from a single generic page (personal
    site, blog, Medium, dev.to, Kaggle, Behance, etc.). Returns None (logged,
    non-fatal) on any failure so the scout pipeline can degrade gracefully.
    Redirects are followed manually with per-hop SSRF validation (see
    _get_validated), so follow_redirects stays off on the client."""
    async with httpx.AsyncClient(timeout=_PAGE_FETCH_TIMEOUT_SECONDS) as client:
        text, _ = await _fetch_one(client, url, discover_links=False)
    return text


async def fetch_page_with_subpages(url: str) -> dict[str, Any] | None:
    """Fetches a generic page plus up to _MAX_SUBPAGES same-domain sub-pages
    it links to (About/Projects/Resume/etc.) - a thin homepage often hides
    the actual substance one click away. Depth is fixed at 1 (sub-pages are
    never themselves crawled for further links). Returns None only if the
    root page itself couldn't be fetched; a sub-page failing just means it's
    omitted, same graceful-degradation contract as fetch_page_text. Returns
    {"text": str, "subpages": [{"url": str, "text": str}, ...]}. Redirects
    are followed manually with per-hop SSRF validation (see _get_validated),
    so follow_redirects stays off on the client."""
    async with httpx.AsyncClient(timeout=_PAGE_FETCH_TIMEOUT_SECONDS) as client:
        text, subpage_links = await _fetch_one(client, url, discover_links=True)
        if text is None:
            return None

        subpages = []
        for link in subpage_links:
            sub_text, _ = await _fetch_one(client, link, discover_links=False)
            if sub_text:
                subpages.append({"url": link, "text": sub_text})

        return {"text": text, "subpages": subpages}
