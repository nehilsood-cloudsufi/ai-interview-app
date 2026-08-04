import json

import httpx
import respx

from app.services import web_search_client

TAVILY_BASE_URL = "https://api.tavily.com"


@respx.mock
async def test_low_quality_domain_is_filtered_out(patch_settings):
    patch_settings(
        tavily_api_key="tvly-key",
        tavily_api_base_url=TAVILY_BASE_URL,
        scout_low_quality_domains=("spamfarm.example",),
    )
    respx.post(f"{TAVILY_BASE_URL}/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"title": "Spammy content", "url": "https://spamfarm.example/acme-corp", "content": "junk"},
                    {"title": "Real result", "url": "https://news.example/acme-corp", "content": "real"},
                ]
            },
        )
    )

    results = await web_search_client.search("Acme Corp")

    urls = [r["url"] for r in results]
    assert "https://spamfarm.example/acme-corp" not in urls
    assert "https://news.example/acme-corp" in urls
    assert len(results) == 1


@respx.mock
async def test_linkedin_company_page_matching_company_is_tagged_official(patch_settings):
    patch_settings(tavily_api_key="tvly-key", tavily_api_base_url=TAVILY_BASE_URL)
    respx.post(f"{TAVILY_BASE_URL}/search").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"title": "Acme Corp", "url": "https://www.linkedin.com/company/acme-corp", "content": "..."}]},
        )
    )

    results = await web_search_client.search("Acme Corp", company_name="Acme Corp")

    assert results[0]["source_tier"] == "official"


@respx.mock
async def test_linkedin_company_page_for_a_different_company_is_tagged_external(patch_settings):
    patch_settings(tavily_api_key="tvly-key", tavily_api_base_url=TAVILY_BASE_URL)
    respx.post(f"{TAVILY_BASE_URL}/search").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"title": "IBM", "url": "https://www.linkedin.com/company/ibm", "content": "..."}]},
        )
    )

    results = await web_search_client.search("Acme Corp", company_name="Acme Corp")

    assert results[0]["source_tier"] == "external"


@respx.mock
async def test_prnewswire_url_with_company_name_in_path_is_tagged_official(patch_settings):
    patch_settings(tavily_api_key="tvly-key", tavily_api_base_url=TAVILY_BASE_URL)
    respx.post(f"{TAVILY_BASE_URL}/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Acme Corp announces...",
                        "url": "https://www.prnewswire.com/news-releases/acme-corp-announces-expansion.html",
                        "content": "...",
                    }
                ]
            },
        )
    )

    results = await web_search_client.search("Acme Corp", company_name="Acme Corp")

    assert results[0]["source_tier"] == "official"


@respx.mock
async def test_prnewswire_url_without_company_name_is_tagged_external(patch_settings):
    patch_settings(tavily_api_key="tvly-key", tavily_api_base_url=TAVILY_BASE_URL)
    respx.post(f"{TAVILY_BASE_URL}/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Some other company announces...",
                        "url": "https://www.prnewswire.com/news-releases/other-company-announces-merger.html",
                        "content": "...",
                    }
                ]
            },
        )
    )

    results = await web_search_client.search("Acme Corp", company_name="Acme Corp")

    assert results[0]["source_tier"] == "external"


@respx.mock
async def test_government_domain_is_tagged_external_not_official(patch_settings):
    # A .gov domain (SEC filings, government sources) is Tier 2 - it is NOT
    # one of the three Tier 1 categories (company website, official LinkedIn
    # page, company press release), even though it's a highly credible source.
    patch_settings(tavily_api_key="tvly-key", tavily_api_base_url=TAVILY_BASE_URL)
    respx.post(f"{TAVILY_BASE_URL}/search").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"title": "Acme Corp 10-K", "url": "https://www.sec.gov/cgi-bin/browse-edgar?acme-corp", "content": "..."}]},
        )
    )

    results = await web_search_client.search("Acme Corp", company_name="Acme Corp")

    assert results[0]["source_tier"] == "external"


@respx.mock
async def test_news_and_directory_domains_are_all_tagged_external(patch_settings):
    # Reuters, Forbes, ZoomInfo - none are Tier 1, regardless of reputation.
    patch_settings(tavily_api_key="tvly-key", tavily_api_base_url=TAVILY_BASE_URL)
    respx.post(f"{TAVILY_BASE_URL}/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"title": "Reuters", "url": "https://www.reuters.com/business/acme-corp", "content": "..."},
                    {"title": "Forbes", "url": "https://www.forbes.com/companies/acme-corp", "content": "..."},
                    {"title": "ZoomInfo", "url": "https://www.zoominfo.com/c/acme-corp/12345", "content": "..."},
                ]
            },
        )
    )

    results = await web_search_client.search("Acme Corp", company_name="Acme Corp")

    assert [r["source_tier"] for r in results] == ["external", "external", "external"]


@respx.mock
async def test_no_company_name_tags_everything_external(patch_settings):
    # Without a company_name to match LinkedIn/press-release paths against,
    # even a URL that WOULD otherwise qualify (an official LinkedIn company
    # page) can't be confirmed as Tier 1 - degrades to "external" rather than
    # crashing or leaving the field absent.
    patch_settings(tavily_api_key="tvly-key", tavily_api_base_url=TAVILY_BASE_URL)
    respx.post(f"{TAVILY_BASE_URL}/search").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"title": "Acme Corp", "url": "https://www.linkedin.com/company/acme-corp", "content": "..."}]},
        )
    )

    results = await web_search_client.search("Acme Corp")

    assert len(results) == 1
    assert results[0]["source_tier"] == "external"


def test_unverified_string_does_not_appear_in_web_search_client_source():
    # Static guard: "unverified" was the old third-tier label (retired in
    # favor of the two-tier official/external system) - a regression here
    # would silently reintroduce it as a source_tier value.
    import inspect

    from app.services import web_search_client as wsc

    assert "unverified" not in inspect.getsource(wsc)


@respx.mock
async def test_time_range_is_included_in_request_payload_when_passed(patch_settings):
    patch_settings(tavily_api_key="tvly-key", tavily_api_base_url=TAVILY_BASE_URL)
    route = respx.post(f"{TAVILY_BASE_URL}/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    await web_search_client.search("Acme Corp revenue", time_range="y")

    sent_payload = json.loads(route.calls.last.request.content)
    assert sent_payload["time_range"] == "y"


@respx.mock
async def test_omitting_time_range_preserves_old_payload_exactly(patch_settings):
    patch_settings(tavily_api_key="tvly-key", tavily_api_base_url=TAVILY_BASE_URL)
    route = respx.post(f"{TAVILY_BASE_URL}/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    await web_search_client.search("Acme Corp overview")

    sent_payload = json.loads(route.calls.last.request.content)
    assert "time_range" not in sent_payload
    assert sent_payload == {"query": "Acme Corp overview", "max_results": web_search_client._MAX_RESULTS}


@respx.mock
async def test_non_json_200_response_returns_empty_not_raises(patch_settings):
    # The never-raises contract must hold even for a 200 with a non-JSON body
    # (proxy interception, an HTML error page, etc.) - response.json() raises
    # JSONDecodeError, which is NOT an httpx.HTTPError; an escape here would
    # abort the whole scout pipeline.
    patch_settings(tavily_api_key="tvly-key", tavily_api_base_url=TAVILY_BASE_URL)
    respx.post(f"{TAVILY_BASE_URL}/search").mock(
        return_value=httpx.Response(200, text="<html>Service temporarily unavailable</html>")
    )

    assert await web_search_client.search("Acme Corp") == []


@respx.mock
async def test_non_dict_result_items_are_skipped(patch_settings):
    patch_settings(tavily_api_key="tvly-key", tavily_api_base_url=TAVILY_BASE_URL)
    respx.post(f"{TAVILY_BASE_URL}/search").mock(
        return_value=httpx.Response(
            200,
            json={"results": ["not-a-dict", {"title": "Real", "url": "https://news.example/acme", "content": "..."}]},
        )
    )

    results = await web_search_client.search("Acme Corp")

    assert [r["url"] for r in results] == ["https://news.example/acme"]


@respx.mock
async def test_press_release_subdomain_is_tagged_official(patch_settings):
    # Suffix-aware like every other domain list in this module: a
    # subdomain of a press-release wire (e.g. ir.businesswire.com) still
    # counts as businesswire.com.
    patch_settings(tavily_api_key="tvly-key", tavily_api_base_url=TAVILY_BASE_URL)
    respx.post(f"{TAVILY_BASE_URL}/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Acme Corp",
                        "url": "https://ir.businesswire.com/news/acme-corp-quarterly-results",
                        "content": "...",
                    }
                ]
            },
        )
    )

    results = await web_search_client.search("Acme Corp", company_name="Acme Corp")

    assert results[0]["source_tier"] == "official"


@respx.mock
async def test_low_quality_subdomain_is_filtered(patch_settings):
    patch_settings(
        tavily_api_key="tvly-key",
        tavily_api_base_url=TAVILY_BASE_URL,
        scout_low_quality_domains=("spamfarm.example",),
    )
    respx.post(f"{TAVILY_BASE_URL}/search").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"title": "S", "url": "https://m.spamfarm.example/acme", "content": "junk"}]},
        )
    )

    assert await web_search_client.search("Acme Corp") == []
