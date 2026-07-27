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
async def test_trusted_domain_is_tagged_trusted(patch_settings):
    patch_settings(
        tavily_api_key="tvly-key",
        tavily_api_base_url=TAVILY_BASE_URL,
        scout_trusted_domains=("trusted-source.example",),
    )
    respx.post(f"{TAVILY_BASE_URL}/search").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"title": "Trusted", "url": "https://www.trusted-source.example/acme", "content": "..."}]},
        )
    )

    results = await web_search_client.search("Acme Corp")

    assert results[0]["source_tier"] == "trusted"


@respx.mock
async def test_unrecognized_domain_is_tagged_unverified(patch_settings):
    patch_settings(
        tavily_api_key="tvly-key",
        tavily_api_base_url=TAVILY_BASE_URL,
        scout_trusted_domains=("trusted-source.example",),
    )
    respx.post(f"{TAVILY_BASE_URL}/search").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"title": "Random blog", "url": "https://randomblog.example/acme", "content": "..."}]},
        )
    )

    results = await web_search_client.search("Acme Corp")

    assert results[0]["source_tier"] == "unverified"


@respx.mock
async def test_no_domain_config_still_tags_everything_unverified(patch_settings):
    # Both tuples empty (the shipped default) - nothing filtered, everything
    # tagged "unverified" rather than crashing or leaving the field absent.
    patch_settings(
        tavily_api_key="tvly-key",
        tavily_api_base_url=TAVILY_BASE_URL,
        scout_low_quality_domains=(),
        scout_trusted_domains=(),
    )
    respx.post(f"{TAVILY_BASE_URL}/search").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"title": "Anything", "url": "https://anywhere.example/acme", "content": "..."}]},
        )
    )

    results = await web_search_client.search("Acme Corp")

    assert len(results) == 1
    assert results[0]["source_tier"] == "unverified"


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
