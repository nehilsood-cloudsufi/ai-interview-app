import httpx
import pytest
import respx

from app.services import page_fetcher

# --- _is_public_url (the SSRF guard) -----------------------------------------
# IP-literal cases need no DNS; hostname cases monkeypatch the resolver.


async def test_is_public_url_rejects_non_http_schemes():
    assert await page_fetcher._is_public_url("ftp://93.184.216.34/file") is False
    assert await page_fetcher._is_public_url("file:///etc/passwd") is False


async def test_is_public_url_rejects_loopback_private_and_metadata_ips():
    assert await page_fetcher._is_public_url("http://127.0.0.1/admin") is False
    assert await page_fetcher._is_public_url("http://10.0.0.5/internal") is False
    assert await page_fetcher._is_public_url("http://192.168.1.1/router") is False
    assert await page_fetcher._is_public_url("http://169.254.169.254/computeMetadata/v1/") is False
    assert await page_fetcher._is_public_url("http://[::1]/") is False


async def test_is_public_url_accepts_global_ip():
    assert await page_fetcher._is_public_url("https://93.184.216.34/") is True


async def test_is_public_url_rejects_hostname_resolving_to_private_ip(monkeypatch):
    async def fake_getaddrinfo(host, port, **kwargs):
        return [(2, 1, 6, "", ("10.0.0.9", 0))]

    import asyncio

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)

    assert await page_fetcher._is_public_url("https://internal.corp.example/") is False


async def test_is_public_url_rejects_unresolvable_hostname(monkeypatch):
    async def fake_getaddrinfo(host, port, **kwargs):
        raise OSError("Name or service not known")

    import asyncio

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)

    assert await page_fetcher._is_public_url("https://does-not-exist.example/") is False


# --- fetch_page_text with the guard applied ----------------------------------


@pytest.fixture
def allow_all_hosts(monkeypatch):
    """Bypasses the DNS/IP check so respx-mocked fetches of example hostnames
    work; the per-URL blocking behavior is tested separately above."""

    async def always_public(url):
        return url.startswith(("http://", "https://"))

    monkeypatch.setattr(page_fetcher, "_is_public_url", always_public)


@respx.mock
async def test_fetch_page_text_blocked_url_makes_no_http_call():
    # 127.0.0.1 fails _is_public_url before any request is built - respx has
    # no routes, so an attempted call would error loudly.
    assert await page_fetcher.fetch_page_text("http://127.0.0.1/secrets") is None
    assert len(respx.calls) == 0


@respx.mock
async def test_fetch_page_text_extracts_visible_text(allow_all_hosts):
    respx.get("https://acme.example.com/").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body><main><p>Acme builds widgets.</p></main><script>ignore()</script></body></html>",
        )
    )

    text = await page_fetcher.fetch_page_text("https://acme.example.com/")

    assert text == "Acme builds widgets."


@respx.mock
async def test_fetch_page_text_blocks_redirect_to_private_address(monkeypatch):
    # First hop is allowed, the redirect target is loopback - the manual
    # redirect loop must re-validate every hop and refuse to follow it.
    async def public_except_loopback(url):
        return "127.0.0.1" not in url

    monkeypatch.setattr(page_fetcher, "_is_public_url", public_except_loopback)
    respx.get("https://acme.example.com/").mock(
        return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/latest/meta-data"})
    )

    text = await page_fetcher.fetch_page_text("https://acme.example.com/")

    assert text is None
    assert len(respx.calls) == 1  # only the public first hop was fetched


@respx.mock
async def test_fetch_page_text_follows_public_redirect(allow_all_hosts):
    respx.get("https://acme.example.com/").mock(
        return_value=httpx.Response(301, headers={"location": "https://www.acme.example.com/"})
    )
    respx.get("https://www.acme.example.com/").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, text="<html><body>Acme home</body></html>"
        )
    )

    text = await page_fetcher.fetch_page_text("https://acme.example.com/")

    assert text == "Acme home"


@respx.mock
async def test_fetch_page_text_gives_up_after_max_redirects(allow_all_hosts):
    respx.get("https://loop.example.com/").mock(
        return_value=httpx.Response(302, headers={"location": "https://loop.example.com/"})
    )

    text = await page_fetcher.fetch_page_text("https://loop.example.com/")

    assert text is None


@respx.mock
async def test_fetch_page_text_caps_oversized_body(allow_all_hosts, monkeypatch):
    monkeypatch.setattr(page_fetcher, "_MAX_RESPONSE_BYTES", 100)
    huge = "<html><body>" + "word " * 10_000 + "</body></html>"
    respx.get("https://big.example.com/").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, text=huge)
    )

    text = await page_fetcher.fetch_page_text("https://big.example.com/")

    # Truncated read still parses; the returned text is bounded by the cap.
    assert text is not None
    assert len(text) < 200


@respx.mock
async def test_fetch_page_text_survives_invalid_url():
    # httpx.InvalidURL is NOT an httpx.HTTPError subclass - it used to escape
    # the narrow catch and discard the entire scout report upstream.
    assert await page_fetcher.fetch_page_text("http://[malformed-ipv6/") is None
    assert len(respx.calls) == 0


@respx.mock
async def test_subpage_discovery_tolerates_www_mismatch(allow_all_hosts):
    # A www. homepage linking to bare-domain About pages (or vice versa) is
    # the same site - the raw netloc comparison used to drop them all.
    respx.get("https://www.acme.example.com/").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<html><body><a href="https://acme.example.com/about">About us</a><main>Home</main></body></html>',
        )
    )
    respx.get("https://acme.example.com/about").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, text="<html><body>About Acme</body></html>"
        )
    )

    result = await page_fetcher.fetch_page_with_subpages("https://www.acme.example.com/")

    assert result is not None
    assert [sub["url"] for sub in result["subpages"]] == ["https://acme.example.com/about"]
