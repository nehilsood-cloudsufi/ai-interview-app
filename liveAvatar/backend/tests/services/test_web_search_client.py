import httpx
import respx

from app.services import web_search_client

BASE_URL = "https://api.tavily.com"


def _tavily_response():
    return {
        "query": "Jane Doe linkedin",
        "results": [
            {
                "title": "Jane Doe - LinkedIn",
                "url": "https://linkedin.com/in/janedoe",
                "content": "Jane Doe is a software engineer at Acme.",
                "score": 0.9,
            },
            {
                "title": "About Jane",
                "url": "https://example.com/about-jane",
                "content": "Background on Jane Doe.",
                "score": 0.7,
            },
        ],
    }


async def test_search_returns_empty_list_when_no_api_key_configured(patch_settings):
    patch_settings(tavily_api_key=None)

    results = await web_search_client.search("Jane Doe linkedin")

    assert results == []


@respx.mock
async def test_search_happy_path_extracts_titles_urls_snippets(patch_settings):
    patch_settings(tavily_api_key="tvly-key", tavily_api_base_url=BASE_URL)
    route = respx.post(f"{BASE_URL}/search").mock(return_value=httpx.Response(200, json=_tavily_response()))

    results = await web_search_client.search("Jane Doe linkedin")

    assert route.calls[0].request.headers["Authorization"] == "Bearer tvly-key"
    assert route.calls[0].request.content == httpx.Request(
        "POST", f"{BASE_URL}/search", json={"query": "Jane Doe linkedin", "max_results": 5}
    ).content
    assert results == [
        {
            "title": "Jane Doe - LinkedIn",
            "url": "https://linkedin.com/in/janedoe",
            "snippet": "Jane Doe is a software engineer at Acme.",
        },
        {
            "title": "About Jane",
            "url": "https://example.com/about-jane",
            "snippet": "Background on Jane Doe.",
        },
    ]


@respx.mock
async def test_search_returns_empty_list_when_no_results(patch_settings):
    patch_settings(tavily_api_key="tvly-key", tavily_api_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/search").mock(return_value=httpx.Response(200, json={"results": []}))

    results = await web_search_client.search("nobody")

    assert results == []


@respx.mock
async def test_search_skips_results_without_url(patch_settings):
    patch_settings(tavily_api_key="tvly-key", tavily_api_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/search").mock(
        return_value=httpx.Response(200, json={"results": [{"title": "No URL here", "content": "..."}]})
    )

    results = await web_search_client.search("Jane Doe")

    assert results == []


@respx.mock
async def test_search_returns_empty_list_on_http_error(patch_settings):
    patch_settings(tavily_api_key="tvly-key", tavily_api_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/search").mock(return_value=httpx.Response(500))

    results = await web_search_client.search("Jane Doe")

    assert results == []


@respx.mock
async def test_search_returns_empty_list_on_network_error(patch_settings):
    patch_settings(tavily_api_key="tvly-key", tavily_api_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/search").mock(side_effect=httpx.ConnectError("boom"))

    results = await web_search_client.search("Jane Doe")

    assert results == []
