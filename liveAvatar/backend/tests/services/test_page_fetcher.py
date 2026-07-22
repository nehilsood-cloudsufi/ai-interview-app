import httpx
import respx

from app.services import page_fetcher

URL = "https://janedoe.dev"


@respx.mock
async def test_fetch_page_text_extracts_visible_text():
    respx.get(URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><head><style>body{color:red}</style></head>"
            "<body><script>evil()</script><h1>Jane Doe</h1><p>ML Engineer</p></body></html>",
        )
    )

    text = await page_fetcher.fetch_page_text(URL)

    assert text == "Jane Doe ML Engineer"


@respx.mock
async def test_fetch_page_text_returns_none_on_non_html_content():
    respx.get(URL).mock(
        return_value=httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4")
    )

    text = await page_fetcher.fetch_page_text(URL)

    assert text is None


@respx.mock
async def test_fetch_page_text_returns_none_on_http_error():
    respx.get(URL).mock(return_value=httpx.Response(500))

    text = await page_fetcher.fetch_page_text(URL)

    assert text is None


@respx.mock
async def test_fetch_page_text_returns_none_on_network_error():
    respx.get(URL).mock(side_effect=httpx.ConnectError("boom"))

    text = await page_fetcher.fetch_page_text(URL)

    assert text is None


@respx.mock
async def test_fetch_page_text_returns_none_for_empty_body():
    respx.get(URL).mock(return_value=httpx.Response(200, headers={"content-type": "text/html"}, text=""))

    text = await page_fetcher.fetch_page_text(URL)

    assert text is None


@respx.mock
async def test_fetch_page_text_truncates_long_content():
    long_text = "word " * 5000
    respx.get(URL).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, text=f"<body>{long_text}</body>"
        )
    )

    text = await page_fetcher.fetch_page_text(URL)

    assert len(text) <= 12000


@respx.mock
async def test_fetch_page_text_prefers_main_content_over_nav_and_footer():
    respx.get(URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<html><body>"
                "<nav>Home About Contact</nav>"
                "<main><h1>Jane Doe</h1><p>ML Engineer with 5 years experience.</p></main>"
                "<footer>Copyright 2026</footer>"
                "</body></html>"
            ),
        )
    )

    text = await page_fetcher.fetch_page_text(URL)

    assert text == "Jane Doe ML Engineer with 5 years experience."


@respx.mock
async def test_fetch_page_text_prefers_article_when_no_main():
    respx.get(URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body><nav>Home</nav><article>Full article content here.</article></body></html>",
        )
    )

    text = await page_fetcher.fetch_page_text(URL)

    assert text == "Full article content here."


@respx.mock
async def test_fetch_page_with_subpages_follows_about_and_projects_links():
    respx.get(URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<html><body>"
                "<nav><a href='/about'>About</a><a href='/projects'>Projects</a>"
                "<a href='https://twitter.com/janedoe'>Twitter</a></nav>"
                "<main>Jane Doe - ML Engineer</main>"
                "</body></html>"
            ),
        )
    )
    respx.get(f"{URL}/about").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, text="<main>About Jane</main>")
    )
    respx.get(f"{URL}/projects").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, text="<main>Cool projects here</main>")
    )

    result = await page_fetcher.fetch_page_with_subpages(URL)

    assert result["text"] == "Jane Doe - ML Engineer"
    subpage_urls = {sp["url"] for sp in result["subpages"]}
    assert subpage_urls == {f"{URL}/about", f"{URL}/projects"}
    # Cross-domain links (Twitter) are never followed, even if keyword-matched.
    assert not any("twitter.com" in sp["url"] for sp in result["subpages"])


@respx.mock
async def test_fetch_page_with_subpages_caps_at_max_subpages():
    respx.get(URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<html><body><nav>"
                "<a href='/about'>About</a><a href='/about-work'>Work</a>"
                "<a href='/resume'>Resume</a><a href='/cv'>CV</a>"
                "</nav><main>Home</main></body></html>"
            ),
        )
    )
    for path in ("/about", "/about-work", "/resume", "/cv"):
        respx.get(f"{URL}{path}").mock(
            return_value=httpx.Response(200, headers={"content-type": "text/html"}, text="<main>x</main>")
        )

    result = await page_fetcher.fetch_page_with_subpages(URL)

    assert len(result["subpages"]) == 3  # _MAX_SUBPAGES


@respx.mock
async def test_fetch_page_with_subpages_returns_none_when_root_fetch_fails():
    respx.get(URL).mock(return_value=httpx.Response(500))

    result = await page_fetcher.fetch_page_with_subpages(URL)

    assert result is None


@respx.mock
async def test_fetch_page_with_subpages_omits_failed_subpage_without_failing_root():
    respx.get(f"{URL}/about").mock(return_value=httpx.Response(500))
    respx.get(URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body><nav><a href='/about'>About</a></nav><main>Home</main></body></html>",
        )
    )

    result = await page_fetcher.fetch_page_with_subpages(URL)

    assert result["text"] == "Home"
    assert result["subpages"] == []
