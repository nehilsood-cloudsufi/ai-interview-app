import asyncio
import json
import time

import httpx
import pytest
import respx

from app.services import data_scout_agent

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def test_trunc_leaves_short_text_unchanged():
    assert data_scout_agent._trunc("short", 100) == "short"


def test_trunc_returns_none_unchanged():
    assert data_scout_agent._trunc(None, 100) is None


def test_trunc_cuts_long_text_and_notes_omitted_count():
    text = "x" * 150
    result = data_scout_agent._trunc(text, 100)
    assert result.startswith("x" * 100)
    assert "50 chars omitted" in result


def test_extract_urls_finds_and_dedupes_across_blobs():
    blobs = [
        "Check us out at https://acme.example.com and https://linkedin.com/company/acme.",
        "Same site again: https://acme.example.com (great place).",
    ]
    urls = data_scout_agent._extract_urls(blobs)
    assert urls == sorted(["https://acme.example.com", "https://linkedin.com/company/acme"])


def test_extract_urls_ignores_blank_blobs():
    assert data_scout_agent._extract_urls([None, "", "no links here"]) == []


def test_truncate_sources_only_includes_keys_present_in_input():
    # An empty input must produce an empty output - omitted keys must never
    # be synthesized back in as empty lists (that would bias the report
    # toward "no GitHub/no targeted search" sections for every company).
    assert data_scout_agent._truncate_sources({}) == {}


def test_truncate_sources_caps_page_link_lookup_and_search_text():
    sources = {
        "github": [{"username": "acmecorp"}],
        "pages": [
            {
                "url": "https://acme.example.com",
                "text": "y" * 5000,
                "subpages": [{"url": "https://acme.example.com/about", "text": "z" * 5000}],
            }
        ],
        "link_lookups": [
            {"url": "https://linkedin.com/company/acme", "domain": "linkedin.com", "results": [{"title": "T", "url": "U", "snippet": "S", "text": "x" * 5000}], "full_profile_accessible": False}
        ],
        "blind_search": [{"query": "Acme Corp overview", "results": [{"title": "T", "url": "U", "snippet": "S", "text": "w" * 5000}]}],
        "representative_search": [{"query": "Jane Doe Acme Corp", "results": [{"title": "T", "url": "U", "snippet": "S", "text": "r" * 5000}]}],
        "targeted_search": [{"claim": "Acme has 50 employees", "results": [{"title": "T", "url": "U", "snippet": "S", "text": "v" * 5000}]}],
    }

    truncated = data_scout_agent._truncate_sources(sources)

    assert truncated["github"] == sources["github"]  # untouched, already bounded at the source
    assert len(truncated["pages"][0]["text"]) < 5000
    assert len(truncated["pages"][0]["subpages"][0]["text"]) < 5000
    assert len(truncated["link_lookups"][0]["results"][0]["text"]) < 5000
    assert len(truncated["blind_search"][0]["results"][0]["text"]) < 5000
    assert len(truncated["representative_search"][0]["results"][0]["text"]) < 5000
    assert len(truncated["targeted_search"][0]["results"][0]["text"]) < 5000
    # Structural fields (non-text) must survive untouched for citation purposes.
    assert truncated["link_lookups"][0]["domain"] == "linkedin.com"
    assert truncated["representative_search"][0]["query"] == "Jane Doe Acme Corp"
    assert truncated["targeted_search"][0]["claim"] == "Acme has 50 employees"


def test_truncate_sources_passes_through_interview_claims_unmodified():
    # The claim-focused report format requires the synthesis call to see
    # every claim (Section 1: Claim Analysis maps each one to its sources),
    # so - unlike the prior peer-sections design - this key now passes
    # through untouched (short strings, no per-item cap needed).
    truncated = data_scout_agent._truncate_sources({"interview_claims": ["a claim"]})
    assert truncated["interview_claims"] == ["a claim"]


@respx.mock
async def test_synthesize_internet_findings_sends_truncated_payload_with_company_header(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "## Internet Findings\n..."}}]})
    )
    sources = {"pages": [{"url": "https://acme.example.com", "text": "p" * 10_000, "subpages": []}]}

    findings = await data_scout_agent._synthesize_internet_findings("Acme Corp", "https://acme.example.com", sources)

    assert findings == "## Internet Findings\n..."
    sent_body = json.loads(route.calls[0].request.content)
    user_message = sent_body["messages"][1]["content"]
    assert "Acme Corp" in user_message
    assert "https://acme.example.com" in user_message
    assert "chars omitted" in user_message
    assert len(user_message) < 10_000  # confirms the page text was actually capped, not passed through raw


@respx.mock
async def test_synthesize_internet_findings_retries_on_503_then_succeeds(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    monkeypatch.setattr(data_scout_agent, "_SYNTHESIS_RETRY_BACKOFF_SECONDS", 0)
    route = respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]}),
        ]
    )

    findings = await data_scout_agent._synthesize_internet_findings("Acme Corp", None, {})

    assert findings == "report"
    assert route.call_count == 2


@respx.mock
async def test_synthesize_internet_findings_does_not_retry_on_4xx(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    monkeypatch.setattr(data_scout_agent, "_SYNTHESIS_RETRY_BACKOFF_SECONDS", 0)
    route = respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(return_value=httpx.Response(400))

    with pytest.raises(httpx.HTTPStatusError):
        await data_scout_agent._synthesize_internet_findings("Acme Corp", None, {})

    assert route.call_count == 1


@respx.mock
async def test_synthesize_internet_findings_raises_after_exhausting_retries(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    monkeypatch.setattr(data_scout_agent, "_SYNTHESIS_RETRY_BACKOFF_SECONDS", 0)
    route = respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(return_value=httpx.Response(503))

    with pytest.raises(httpx.HTTPStatusError):
        await data_scout_agent._synthesize_internet_findings("Acme Corp", None, {})

    assert route.call_count == data_scout_agent._SYNTHESIS_MAX_ATTEMPTS


@respx.mock
async def test_synthesize_internet_findings_receives_interview_claims(patch_settings):
    # The claim-focused report format requires the synthesis call to see
    # interview_claims directly - unlike the old peer-sections design, which
    # deliberately withheld it.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )
    sources = {"interview_claims": ["We have 50 employees"], "targeted_search": [{"claim": "We have 50 employees", "results": []}]}

    await data_scout_agent._synthesize_internet_findings("Acme Corp", None, sources)

    sent_body = json.loads(route.calls[0].request.content)
    user_message = sent_body["messages"][1]["content"]
    assert "We have 50 employees" in user_message


# --- Website derivation ----------------------------------------------------


async def test_derive_company_website_prefers_url_in_extra_context():
    website = await data_scout_agent._derive_company_website(
        "Acme Corp", "Learn more at https://acme.example.com about us.", None
    )
    assert website == "https://acme.example.com"


async def test_derive_company_website_finds_url_in_documents():
    website = await data_scout_agent._derive_company_website(
        "Acme Corp", None, ["Company profile.", "Our site is https://acme.example.com"]
    )
    assert website == "https://acme.example.com"


@respx.mock
async def test_derive_company_website_falls_back_to_search(monkeypatch):
    async def fake_search(query, **kwargs):
        assert query == "Acme Corp official website"
        return [{"title": "Acme", "url": "https://www.acme.example.com/home", "snippet": "..."}]

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await data_scout_agent._derive_company_website("Acme Corp", None, None)

    # _domain_of strips a leading "www." by design (consistent with the rest
    # of the module), so the derived website is the bare domain.
    assert website == "https://acme.example.com"


async def test_derive_company_website_returns_none_when_nothing_found(monkeypatch):
    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await data_scout_agent._derive_company_website("Acme Corp", None, None)

    assert website is None


@respx.mock
async def test_derive_company_website_skips_excluded_domain_top_hit(monkeypatch):
    # A company's LinkedIn page frequently outranks its actual site for a
    # generic "official website" query - it must never be picked as
    # company_website (which gets fetched directly, unlike LinkedIn's own
    # safer search-based handling elsewhere in this module).
    async def fake_search(query, **kwargs):
        return [
            {"title": "Acme Corp - LinkedIn", "url": "https://www.linkedin.com/company/acme", "snippet": "..."},
            {"title": "Acme", "url": "https://www.acme.example.com/home", "snippet": "..."},
        ]

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await data_scout_agent._derive_company_website("Acme Corp", None, None)

    assert website == "https://acme.example.com"


async def test_derive_company_website_returns_none_when_every_hit_excluded(monkeypatch):
    async def fake_search(query, **kwargs):
        return [
            {"title": "Acme Corp - LinkedIn", "url": "https://www.linkedin.com/company/acme", "snippet": "..."},
            {"title": "Acme Corp (@acme) - X", "url": "https://twitter.com/acme", "snippet": "..."},
        ]

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await data_scout_agent._derive_company_website("Acme Corp", None, None)

    assert website is None


async def test_derive_company_website_returns_top_hit_when_not_excluded(monkeypatch):
    # Happy path still works: a non-excluded top hit is returned immediately,
    # without needing to look at any further hits.
    async def fake_search(query, **kwargs):
        return [
            {"title": "Acme", "url": "https://www.acme.example.com/home", "snippet": "..."},
            {"title": "Acme Corp - LinkedIn", "url": "https://www.linkedin.com/company/acme", "snippet": "..."},
        ]

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await data_scout_agent._derive_company_website("Acme Corp", None, None)

    assert website == "https://acme.example.com"


async def test_derive_company_website_skips_wikipedia_top_hit(monkeypatch):
    # A high-authority reference site (Wikipedia) frequently outranks a
    # company's actual site for a generic "official website" query - it
    # must never be picked as company_website, same reasoning as the
    # LinkedIn case (fetched directly, unlike a genuinely third-party
    # source). en.wikipedia.org must match via the wikipedia.org suffix.
    async def fake_search(query, **kwargs):
        return [
            {"title": "Zensar Technologies - Wikipedia", "url": "https://en.wikipedia.org/wiki/Zensar_Technologies", "snippet": "..."},
            {"title": "Zensar", "url": "https://www.zensar.com/home", "snippet": "..."},
        ]

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await data_scout_agent._derive_company_website("Zensar Technologies", None, None)

    assert website == "https://zensar.com"


async def test_derive_company_website_returns_none_when_every_hit_excluded_including_reference_sites(monkeypatch):
    async def fake_search(query, **kwargs):
        return [
            {"title": "Acme Corp - LinkedIn", "url": "https://www.linkedin.com/company/acme", "snippet": "..."},
            {"title": "Acme Corp - Wikipedia", "url": "https://en.wikipedia.org/wiki/Acme_Corp", "snippet": "..."},
        ]

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await data_scout_agent._derive_company_website("Acme Corp", None, None)

    assert website is None


async def test_derive_company_website_skips_wikipedia_url_mentioned_in_extra_context():
    # A literal Wikipedia URL mentioned in extra_context/documents must not
    # be treated as the company's own site either - same exclusion applies
    # to the text-mentioned-URL path, not just the search fallback.
    website = await data_scout_agent._derive_company_website(
        "Zensar Technologies",
        "See https://en.wikipedia.org/wiki/Zensar_Technologies and https://www.zensar.com for more.",
        None,
    )
    assert website == "https://www.zensar.com"


# --- Representative search --------------------------------------------------


async def test_gather_representative_search_runs_query_and_returns_entry(monkeypatch):
    async def fake_search(query, **kwargs):
        assert query == "Jane Doe Acme Corp"
        return [{"title": "Jane Doe - LinkedIn", "url": "https://linkedin.com/in/janedoe", "snippet": "VP Sales"}]

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)

    result = await data_scout_agent._gather_representative_search("Jane Doe", "Acme Corp")

    assert result == [
        {
            "query": "Jane Doe Acme Corp",
            "results": [{"title": "Jane Doe - LinkedIn", "url": "https://linkedin.com/in/janedoe", "snippet": "VP Sales", "text": None}],
        }
    ]


async def test_gather_representative_search_returns_empty_without_hits(monkeypatch):
    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    assert await data_scout_agent._gather_representative_search("Jane Doe", "Acme Corp") == []


async def test_gather_representative_search_returns_empty_without_name():
    assert await data_scout_agent._gather_representative_search("", "Acme Corp") == []


# --- Claim extraction (transcript + extra_context + documents) -------------


@respx.mock
async def test_extract_claims_happy_path(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "claims": [
                                        {"claim": "We have 50 employees", "search_query": "50 employees", "source": "transcript"},
                                        {"claim": "We opened a Boston office in 2024", "search_query": "Boston office 2024", "source": "transcript"},
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )
    )

    claims = await data_scout_agent._extract_claims("We have 50 employees. We opened a Boston office in 2024.", None, None)

    assert [c["claim"] for c in claims] == ["We have 50 employees", "We opened a Boston office in 2024"]
    assert claims[0]["search_query"] == "50 employees"
    assert claims[0]["source"] == "transcript"


@respx.mock
async def test_extract_claims_combines_transcript_context_and_documents(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": []})}}]})
    )

    await data_scout_agent._extract_claims(
        "We have 50 employees.", "We were founded in 2010.", ["We are ISO 9001 certified."]
    )

    sent_body = json.loads(route.calls[0].request.content)
    user_message = sent_body["messages"][1]["content"]
    assert "Interview Transcript" in user_message
    assert "We have 50 employees." in user_message
    assert "Additional Context" in user_message
    assert "We were founded in 2010." in user_message
    assert "Attached Document 1" in user_message
    assert "We are ISO 9001 certified." in user_message


@respx.mock
async def test_extract_claims_works_from_extra_context_alone(patch_settings):
    # No transcript yet (pre-interview) - extra_context alone must still
    # trigger extraction.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"claims": [{"claim": "We are ISO certified", "search_query": "ISO certified", "source": "context"}]})}}]},
        )
    )

    claims = await data_scout_agent._extract_claims(None, "We are ISO certified.", None)

    assert [c["claim"] for c in claims] == ["We are ISO certified"]
    assert claims[0]["source"] == "context"


@respx.mock
async def test_extract_claims_tolerates_plain_string_claims(patch_settings):
    # Defensive: if the model ignores the object shape and returns bare
    # strings, fall back to using the claim text as its own search query.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": ["A bare claim"]})}}]})
    )

    claims = await data_scout_agent._extract_claims("Some transcript.", None, None)

    assert claims == [{"claim": "A bare claim", "search_query": "A bare claim", "source": "transcript"}]


@respx.mock
async def test_extract_claims_strips_markdown_fences(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    fenced = "```json\n" + json.dumps({"claims": [{"claim": "A claim", "search_query": "a claim", "source": "transcript"}]}) + "\n```"
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": fenced}}]})
    )

    claims = await data_scout_agent._extract_claims("Some transcript text.", None, None)

    assert [c["claim"] for c in claims] == ["A claim"]


async def test_extract_claims_returns_empty_without_api_key(patch_settings):
    patch_settings(gemini_api_key=None)
    assert await data_scout_agent._extract_claims("Some transcript.", None, None) == []


async def test_extract_claims_returns_empty_when_all_inputs_blank(patch_settings):
    patch_settings(gemini_api_key="gem-key")
    assert await data_scout_agent._extract_claims("   ", "  ", [" ", ""]) == []


@respx.mock
async def test_extract_claims_returns_empty_on_http_error(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(return_value=httpx.Response(500))

    assert await data_scout_agent._extract_claims("Some transcript.", None, None) == []


@respx.mock
async def test_extract_claims_returns_empty_on_malformed_json(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "not json at all"}}]})
    )

    assert await data_scout_agent._extract_claims("Some transcript.", None, None) == []


# --- run_scout: blind gathering ---------------------------------------------


@respx.mock
async def test_run_scout_gathers_blind_search_queries(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    queries = []

    async def fake_search(query, **kwargs):
        queries.append(query)
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None, None)

    assert "Acme Corp overview" in queries
    assert "Acme Corp latest news" in queries
    assert "Acme Corp revenue" in queries
    assert "Acme Corp employees" in queries
    assert "Acme Corp leadership" in queries
    assert "Acme Corp reviews" in queries
    assert "Acme Corp controversies" in queries
    assert "Acme Corp clients" in queries
    # Industry-neutral: none of tech-specific wording leaks into the queries.
    assert not any("tech stack" in q or "engineering" in q for q in queries)


@respx.mock
async def test_run_scout_derives_website_from_extra_context_and_fetches_it(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_fetch_page_with_subpages(url):
        assert url == "https://acme.example.com"
        return {"text": "Acme Corp builds widgets.", "subpages": [{"url": "https://acme.example.com/about", "text": "About Acme"}]}

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.page_fetcher.fetch_page_with_subpages", fake_fetch_page_with_subpages)
    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout(
        "Acme Corp", "Jane Doe", None, "Visit us at https://acme.example.com", None, None
    )

    assert result["company_website"] == "https://acme.example.com"
    assert result["sources"]["pages"] == [
        {
            "url": "https://acme.example.com",
            "text": "Acme Corp builds widgets.",
            "source_tier": "official",
            "subpages": [
                {"url": "https://acme.example.com/about", "text": "About Acme", "source_tier": "official"}
            ],
        }
    ]
    # The derived company website is the most credible source available -
    # tagged "official" on both the top-level page and its subpages.
    assert result["sources"]["pages"][0]["source_tier"] == "official"
    assert result["sources"]["pages"][0]["subpages"][0]["source_tier"] == "official"


@respx.mock
async def test_run_scout_derives_website_from_documents(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_fetch_page_with_subpages(url):
        assert url == "https://acme.example.com"
        return {"text": "Acme Corp builds widgets.", "subpages": []}

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.page_fetcher.fetch_page_with_subpages", fake_fetch_page_with_subpages)
    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout(
        "Acme Corp", "Jane Doe", None, None, ["Our company profile: https://acme.example.com"], None
    )

    assert result["company_website"] == "https://acme.example.com"


@respx.mock
async def test_run_scout_derives_website_via_search_when_no_url_provided(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_fetch_page_with_subpages(url):
        assert url == "https://acme.example.com"
        return {"text": "Acme Corp builds widgets.", "subpages": []}

    async def fake_search(query, **kwargs):
        if query == "Acme Corp official website":
            return [{"title": "Acme", "url": "https://acme.example.com/home", "snippet": "..."}]
        return []

    monkeypatch.setattr("app.services.page_fetcher.fetch_page_with_subpages", fake_fetch_page_with_subpages)
    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None, None)

    assert result["company_website"] == "https://acme.example.com"


@respx.mock
async def test_run_scout_omits_pages_key_when_no_website_derivable(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None, None)

    assert "pages" not in result["sources"]
    assert result["company_website"] is None


@respx.mock
async def test_run_scout_gathers_representative_search(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        if query == "Jane Doe Acme Corp":
            return [{"title": "Jane Doe - LinkedIn", "url": "https://linkedin.com/in/janedoe", "snippet": "VP Sales at Acme"}]
        return []

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", "VP Sales", None, None, None)

    assert result["sources"]["representative_search"] == [
        {
            "query": "Jane Doe Acme Corp",
            "results": [{"title": "Jane Doe - LinkedIn", "url": "https://linkedin.com/in/janedoe", "snippet": "VP Sales at Acme", "text": None}],
        }
    ]


@respx.mock
async def test_run_scout_omits_github_key_when_no_github_url_discovered(patch_settings, monkeypatch):
    # The actual bias this guards against: a law firm, restaurant chain, or
    # any non-technical company must never get an empty/negative GitHub
    # section just because it has no GitHub presence.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": []})}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]}),
        ]
    )

    result = await data_scout_agent.run_scout(
        "Doe & Associates Law Firm", "John Doe", None, "We handle corporate litigation.", None, None
    )

    assert "github" not in result["sources"]


@respx.mock
async def test_run_scout_gathers_github_when_url_found_in_extra_context(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_fetch_profile(username):
        assert username == "acmecorp"
        return {"username": "acmecorp", "public_repos": 3}

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.github_client.fetch_profile", fake_fetch_profile)
    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    # Two Gemini calls: claim extraction (Pass B), then synthesis.
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": []})}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]}),
        ]
    )

    result = await data_scout_agent.run_scout(
        "Acme Corp", "Jane Doe", None, "Our code is open source at https://github.com/acmecorp.", None, None
    )

    assert result["sources"]["github"] == [{"username": "acmecorp", "public_repos": 3}]


@respx.mock
async def test_run_scout_does_not_gather_github_from_transcript_alone(patch_settings, monkeypatch):
    # Pass A must never let the transcript drive a search/fetch decision -
    # a GitHub URL that only appears in the transcript (not extra_context,
    # documents, or the website) must not trigger a GitHub lookup.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    fetch_profile_called = False

    async def fake_fetch_profile(username):
        nonlocal fetch_profile_called
        fetch_profile_called = True
        return {"username": username, "public_repos": 1}

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.github_client.fetch_profile", fake_fetch_profile)
    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": []})}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]}),
        ]
    )

    result = await data_scout_agent.run_scout(
        "Acme Corp", "Jane Doe", None, None, None, "Our code is open source at https://github.com/acmecorp."
    )

    assert fetch_profile_called is False
    assert "github" not in result["sources"]


@respx.mock
async def test_run_scout_gathers_github_when_url_found_on_website(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_fetch_page_with_subpages(url):
        return {"text": "Find our code at https://github.com/acmecorp", "subpages": []}

    async def fake_fetch_profile(username):
        return {"username": username, "public_repos": 1}

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.page_fetcher.fetch_page_with_subpages", fake_fetch_page_with_subpages)
    monkeypatch.setattr("app.services.github_client.fetch_profile", fake_fetch_profile)
    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout(
        "Acme Corp", "Jane Doe", None, "Visit us at https://acme.example.com", None, None
    )

    assert result["sources"]["github"] == [{"username": "acmecorp", "public_repos": 1}]


@respx.mock
async def test_run_scout_gathers_link_lookups_for_no_scrape_domains_discovered(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_fetch_page_text(url):
        return "Full LinkedIn page text"

    async def fake_search(query, **kwargs):
        if query.startswith("site:linkedin.com"):
            return [{"title": "Acme - LinkedIn", "url": "https://linkedin.com/company/acme", "snippet": "A company"}]
        return []

    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": []})}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]}),
        ]
    )

    result = await data_scout_agent.run_scout(
        "Acme Corp", "Jane Doe", None, "Follow us at https://linkedin.com/company/acme", None, None
    )

    assert result["sources"]["link_lookups"] == [
        {
            "url": "https://linkedin.com/company/acme",
            "domain": "linkedin.com",
            "results": [
                {
                    "title": "Acme - LinkedIn",
                    "url": "https://linkedin.com/company/acme",
                    "snippet": "A company",
                    "text": "Full LinkedIn page text",
                }
            ],
            "full_profile_accessible": False,
        }
    ]


# --- run_scout: Pass B (transcript / extra_context / documents) ------------


async def test_run_scout_skips_pass_b_entirely_when_no_candidate_input(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    extract_claims_called = False

    async def fake_extract_claims(transcript, extra_context, documents):
        nonlocal extract_claims_called
        extract_claims_called = True
        return []

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr(data_scout_agent, "_extract_claims", fake_extract_claims)
    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    with respx.mock:
        respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
        )
        result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None, None)

    assert extract_claims_called is False
    assert result["interview_claims"] == []
    assert "interview_claims" not in result["sources"]


@respx.mock
async def test_run_scout_gathers_targeted_search_per_claim_when_transcript_given(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    queries = []

    async def fake_search(query, **kwargs):
        queries.append(query)
        if "50 employees" in query:
            return [{"title": "Acme grows", "url": "https://news.example.com/acme-50", "snippet": "Acme now has 50 staff."}]
        return []

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": [{"claim": "We have 50 employees", "search_query": "Acme Corp 50 employees", "source": "transcript"}]})}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]}),
        ]
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None, "We have 50 employees.")

    assert result["interview_claims"] == ["We have 50 employees"]
    assert result["sources"]["interview_claims"] == ["We have 50 employees"]
    assert result["sources"]["targeted_search"] == [
        {
            "claim": "We have 50 employees",
            "results": [{"title": "Acme grows", "url": "https://news.example.com/acme-50", "snippet": "Acme now has 50 staff.", "text": None}],
        }
    ]
    assert "Acme Corp 50 employees" in queries


@respx.mock
async def test_run_scout_extracts_claims_from_extra_context_without_transcript(patch_settings, monkeypatch):
    # Pre-interview: only the intake form's extra_context is available yet.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": [{"claim": "We were founded in 2010", "search_query": "Acme Corp founded 2010", "source": "context"}]})}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]}),
        ]
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, "We were founded in 2010.", None, None)

    assert result["interview_claims"] == ["We were founded in 2010"]


@respx.mock
async def test_run_scout_targeted_search_dedupes_against_pass_a_urls(patch_settings, monkeypatch):
    # The exact behavior this guards against: a URL Pass A already surfaced
    # (blind search) must not be re-listed under targeted_search too. The
    # claim still gets an entry (now required for the Data Gaps section),
    # just with an empty results list once the only hit is deduped away.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    shared_hit = {"title": "Acme overview", "url": "https://news.example.com/acme", "snippet": "..."}

    async def fake_search(query, **kwargs):
        if query == "Acme Corp overview":
            return [shared_hit]
        if query.startswith("Acme Corp We have") or query == "Acme Corp 50 employees":
            return [shared_hit]  # same URL as the blind search already found
        return []

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": [{"claim": "We have 50 employees", "search_query": "Acme Corp 50 employees", "source": "transcript"}]})}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]}),
        ]
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None, "We have 50 employees.")

    assert result["sources"]["targeted_search"] == [{"claim": "We have 50 employees", "results": []}]


async def test_run_scout_soft_fails_when_gemini_key_missing(patch_settings, monkeypatch):
    # run_scout must never raise on a synthesis failure (missing key, quota
    # exhaustion, etc.) - it should return findings_ok=False with whatever
    # sources were gathered, not lose that data by propagating the exception.
    patch_settings(gemini_api_key=None)

    async def fake_search(query, **kwargs):
        return []

    async def fake_fetch_profile(username):
        return {"username": username, "public_repos": 1}

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.github_client.fetch_profile", fake_fetch_profile)

    result = await data_scout_agent.run_scout(
        "Acme Corp", "Jane Doe", None, "Code at https://github.com/acmecorp", None, None
    )

    assert result["findings_ok"] is False
    assert result["internet_findings"] == ""
    # Gathered sources must survive even though synthesis failed.
    assert result["sources"]["github"] == [{"username": "acmecorp", "public_repos": 1}]


@respx.mock
async def test_run_scout_degrades_gracefully_when_website_fetch_fails(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_fetch_page_with_subpages(url):
        return None  # simulates page_fetcher's own internal failure handling

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.page_fetcher.fetch_page_with_subpages", fake_fetch_page_with_subpages)
    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout(
        "Acme Corp", "Jane Doe", None, "Visit us at https://acme.example.com", None, None
    )

    assert "pages" not in result["sources"]


@respx.mock
async def test_run_scout_degrades_gracefully_when_github_lookup_fails(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_fetch_profile(username):
        return None  # simulates github_client's own internal failure handling

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.github_client.fetch_profile", fake_fetch_profile)
    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": []})}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]}),
        ]
    )

    result = await data_scout_agent.run_scout(
        "Acme Corp", "Jane Doe", None, "Code at https://github.com/ghost", None, None
    )

    assert "github" not in result["sources"]
    assert result["internet_findings"] == "report"


# --- Speed improvements ---------------------------------------------------


def test_claim_specificity_score_prioritizes_concrete_facts_over_vague_metrics():
    assert data_scout_agent._claim_specificity_score("We were founded in 2019") > 0
    assert data_scout_agent._claim_specificity_score("We have 50 employees") > 0
    assert data_scout_agent._claim_specificity_score("Our retention rate is 95%") == -1
    assert data_scout_agent._claim_specificity_score("Our growth percentage is strong") == -1
    assert data_scout_agent._claim_specificity_score("We care about quality") == 0


def _claim_obj(text):
    return {"claim": text, "search_query": text, "source": "transcript"}


def test_select_targeted_claims_returns_all_when_under_cap():
    claims = [_claim_obj(f"Claim {i}") for i in range(5)]
    assert data_scout_agent._select_targeted_claims(claims) == claims


def test_select_targeted_claims_caps_at_ten_and_prefers_specific_claims():
    specific = [_claim_obj(f"We opened office number {i} in 20{10 + i}") for i in range(10)]
    vague = [_claim_obj(f"Our retention rate is great, case {i}") for i in range(5)]
    claims = vague + specific  # vague ones come first in extraction order

    selected = data_scout_agent._select_targeted_claims(claims)

    assert len(selected) == 10
    # All 10 specific (numeric/year-bearing) claims made the cut; the vague
    # retention-rate ones (unlikely to have public data) were dropped first.
    assert {c["claim"] for c in selected} == {c["claim"] for c in specific}


def test_select_targeted_claims_preserves_original_order_among_selected():
    # Ties (equal specificity score) keep extraction order - a stable
    # selection, not an arbitrary reshuffle.
    claims = [_claim_obj(f"We have {i} offices") for i in range(15)]
    selected = data_scout_agent._select_targeted_claims(claims)
    assert selected == claims[:10]


@respx.mock
async def test_gather_blind_search_runs_all_eight_queries_concurrently(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    delay = 0.05

    async def fake_search(query, **kwargs):
        await asyncio.sleep(delay)
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    start = time.monotonic()
    await data_scout_agent._gather_blind_search("Acme Corp")
    elapsed = time.monotonic() - start

    # Sequential would take ~8 * delay; concurrent should take ~1 * delay.
    assert elapsed < delay * 4


@respx.mock
async def test_gather_blind_search_biases_only_revenue_employees_leadership_queries(
    patch_settings, monkeypatch
):
    patch_settings(
        gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL, scout_recency_bias_time_range="y"
    )
    calls: list[tuple[str, str | None]] = []

    async def fake_search(query, time_range=None):
        calls.append((query, time_range))
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    await data_scout_agent._gather_blind_search("Acme Corp")

    time_range_by_query = dict(calls)
    assert time_range_by_query["Acme Corp revenue"] == "y"
    assert time_range_by_query["Acme Corp employees"] == "y"
    assert time_range_by_query["Acme Corp leadership"] == "y"
    assert time_range_by_query["Acme Corp overview"] is None
    assert time_range_by_query["Acme Corp latest news"] is None
    assert time_range_by_query["Acme Corp reviews"] is None
    assert time_range_by_query["Acme Corp controversies"] is None
    assert time_range_by_query["Acme Corp clients"] is None


@respx.mock
async def test_gather_targeted_runs_all_claim_queries_concurrently(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    delay = 0.05
    claims = [_claim_obj(f"Claim number {i}") for i in range(6)]

    async def fake_search(query, **kwargs):
        await asyncio.sleep(delay)
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    start = time.monotonic()
    await data_scout_agent._gather_targeted(claims, set(), "Acme Corp")
    elapsed = time.monotonic() - start

    assert elapsed < delay * 3


@respx.mock
async def test_gather_targeted_slices_results_to_three_per_claim(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        return [
            {"title": f"Acme Corp result {i}", "url": f"https://news.example.com/{i}", "snippet": f"snippet {i}"}
            for i in range(5)
        ]

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)

    targeted = await data_scout_agent._gather_targeted([_claim_obj("We have 50 employees")], set(), "Acme Corp")

    assert len(targeted[0]["results"]) == 3


@respx.mock
async def test_run_scout_gathering_timeout_proceeds_with_partial_data(patch_settings, monkeypatch):
    # Simulates Pass A taking longer than the gather deadline - run_scout
    # must still complete (proceeding to synthesis) rather than hang or raise.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    monkeypatch.setattr(data_scout_agent, "_GATHER_TIMEOUT_SECONDS", 0.05)

    async def slow_search(query):
        await asyncio.sleep(1.0)
        return []

    monkeypatch.setattr("app.services.web_search_client.search", slow_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None, None)

    assert result["internet_findings"] == "report"
    assert result["findings_ok"] is True
    assert result["sources"] == {}  # nothing finished gathering before the deadline


# --- PII redaction (_redact_pii) --------------------------------------------


def test_redact_pii_masks_phone_numbers():
    text = "Reach the office at (555) 123-4567 or 555.987.6543 for details."
    redacted = data_scout_agent._redact_pii(text)
    assert "555" not in redacted
    assert "[redacted]" in redacted


def test_redact_pii_masks_personal_email_but_allows_generic_business_email():
    text = "Contact John Smith directly at john.smith@acme.example, or reach us at info@acme.example."
    redacted = data_scout_agent._redact_pii(text)
    assert "john.smith@acme.example" not in redacted
    assert "info@acme.example" in redacted


def test_redact_pii_masks_street_address():
    text = "Our headquarters is at 123 Main Street, Suite 400."
    redacted = data_scout_agent._redact_pii(text)
    assert "123 Main Street" not in redacted
    assert "[redacted]" in redacted


def test_redact_pii_leaves_clean_business_text_unchanged():
    text = "Acme Corp builds ML pipelines for banks and has 200 enterprise customers."
    assert data_scout_agent._redact_pii(text) == text


def test_redact_pii_returns_none_unchanged():
    assert data_scout_agent._redact_pii(None) is None


def test_redact_pii_disabled_via_settings_returns_text_unchanged(patch_settings):
    patch_settings(scout_redact_pii=False)
    text = "Call me directly at 555-123-4567 or john.smith@acme.example."
    assert data_scout_agent._redact_pii(text) == text


@respx.mock
async def test_run_scout_redacts_pii_from_enriched_search_result_text(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        if query == "Acme Corp overview":
            return [
                {
                    "title": "Acme Corp overview",
                    "url": "https://news.example.com/acme",
                    "snippet": "Reach the founder directly at 555-123-4567.",
                }
            ]
        return []

    async def fake_fetch_page_text(url):
        return "Acme Corp was founded by Jane Doe (jane.doe@acme.example)."

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None, None)

    entry = result["sources"]["blind_search"][0]
    assert "555-123-4567" not in entry["results"][0]["snippet"]
    assert "jane.doe@acme.example" not in entry["results"][0]["text"]


@respx.mock
async def test_run_scout_redacts_pii_from_company_website_text(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_fetch_page_with_subpages(url):
        return {
            "text": "Call our CEO directly at (555) 123-4567.",
            "subpages": [{"url": "https://acme.example.com/about", "text": "Email jane.doe@acme.example directly."}],
        }

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.page_fetcher.fetch_page_with_subpages", fake_fetch_page_with_subpages)
    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout(
        "Acme Corp", "Jane Doe", None, "Visit us at https://acme.example.com", None, None
    )

    page = result["sources"]["pages"][0]
    assert "555" not in page["text"]
    assert "jane.doe@acme.example" not in page["subpages"][0]["text"]


# --- Same-company relevance filter (_filter_relevant / _is_relevant_result) --


def test_normalize_company_tokens_strips_suffix_and_punctuation():
    assert data_scout_agent._normalize_company_tokens("Acme Corp, Inc.") == {"acme"}


def test_normalize_company_tokens_returns_empty_set_for_blank_name():
    assert data_scout_agent._normalize_company_tokens("") == set()
    assert data_scout_agent._normalize_company_tokens(None) == set()


def test_is_relevant_result_keeps_result_mentioning_company_name():
    result = {"title": "Acme Corp raises funding", "url": "https://news.example.com/acme", "snippet": "..."}
    assert data_scout_agent._is_relevant_result(result, {"acme"}) is True


def test_is_relevant_result_drops_result_with_no_name_overlap_anywhere():
    result = {"title": "Random Widgets Co. news", "url": "https://news.example.com/widgets", "snippet": "unrelated story"}
    assert data_scout_agent._is_relevant_result(result, {"acme"}) is False


def test_is_relevant_result_matches_via_domain_when_title_snippet_dont_mention_name():
    result = {"title": "Latest updates", "url": "https://acmecorp.example.com/news", "snippet": "..."}
    assert data_scout_agent._is_relevant_result(result, {"acmecorp"}) is True


def test_is_relevant_result_extra_tokens_rescue_a_result_missing_company_name():
    # Representative search: a legitimate LinkedIn hit that only mentions
    # the person's name, not the company, must not be dropped.
    result = {"title": "Jane Doe - LinkedIn", "url": "https://linkedin.com/in/janedoe", "snippet": "VP Sales"}
    assert data_scout_agent._is_relevant_result(result, {"acme"}, {"jane", "doe"}) is True


def test_is_relevant_result_keeps_everything_when_no_tokens_given():
    result = {"title": "Anything at all", "url": "https://example.com/x", "snippet": "..."}
    assert data_scout_agent._is_relevant_result(result, set()) is True


def test_filter_relevant_drops_unrelated_results_keeps_matching_ones():
    results = [
        {"title": "Acme Corp overview", "url": "https://news.example.com/acme", "snippet": "..."},
        {"title": "Unrelated Co. news", "url": "https://news.example.com/other", "snippet": "nothing to do with it"},
    ]
    filtered = data_scout_agent._filter_relevant(results, "Acme Corp")
    assert [r["title"] for r in filtered] == ["Acme Corp overview"]


@respx.mock
async def test_run_scout_blind_search_drops_unrelated_company_result(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        if query == "Acme Corp overview":
            return [
                {"title": "Acme Corp raises funding", "url": "https://news.example.com/acme", "snippet": "..."},
                {"title": "Totally Unrelated Ventures", "url": "https://news.example.com/other", "snippet": "nothing to do with the search subject"},
            ]
        return []

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None, None)

    entry = result["sources"]["blind_search"][0]
    assert len(entry["results"]) == 1
    assert entry["results"][0]["title"] == "Acme Corp raises funding"


@respx.mock
async def test_run_scout_blind_search_keeps_all_results_when_all_relevant(patch_settings, monkeypatch):
    # A clean-data control: relevance filtering must not wrongly drop
    # legitimate results that DO mention the company.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        if query == "Acme Corp overview":
            return [
                {"title": "Acme Corp raises funding", "url": "https://news.example.com/a", "snippet": "..."},
                {"title": "Acme Corp expands to Europe", "url": "https://news.example.com/b", "snippet": "..."},
            ]
        return []

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None, None)

    entry = result["sources"]["blind_search"][0]
    assert len(entry["results"]) == 2


@respx.mock
async def test_run_scout_representative_search_keeps_person_only_result(patch_settings, monkeypatch):
    # A legitimate representative-search hit that doesn't mention the
    # company name at all (common for a bare LinkedIn snippet) must survive
    # relevance filtering via the representative-name OR check.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        if query == "Jane Doe Acme Corp":
            return [{"title": "Jane Doe - LinkedIn", "url": "https://linkedin.com/in/janedoe", "snippet": "VP Sales"}]
        return []

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None, None)

    assert len(result["sources"]["representative_search"][0]["results"]) == 1


@respx.mock
async def test_run_scout_representative_search_drops_result_matching_neither_name(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        if query == "Jane Doe Acme Corp":
            return [
                {"title": "Jane Doe - LinkedIn", "url": "https://linkedin.com/in/janedoe", "snippet": "VP Sales"},
                {"title": "Unrelated Person - LinkedIn", "url": "https://linkedin.com/in/someoneelse", "snippet": "Works at Other Corp"},
            ]
        return []

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None, None)

    results = result["sources"]["representative_search"][0]["results"]
    assert len(results) == 1
    assert results[0]["title"] == "Jane Doe - LinkedIn"


@respx.mock
async def test_run_scout_targeted_search_drops_unrelated_company_result(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        if "50 employees" in query:
            return [
                {"title": "Acme Corp has 50 employees", "url": "https://news.example.com/acme-50", "snippet": "..."},
                {"title": "Unrelated Startup hires 50", "url": "https://news.example.com/other-50", "snippet": "no relation to the search subject"},
            ]
        return []

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": [{"claim": "We have 50 employees", "search_query": "Acme Corp 50 employees", "source": "transcript"}]})}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]}),
        ]
    )

    result = await data_scout_agent.run_scout(
        "Acme Corp", "Jane Doe", None, None, None, "Interviewer: How many employees? Candidate: We have 50 employees, all remote."
    )

    targeted_entry = next(
        (e for e in result["sources"].get("targeted_search", []) if e["results"]), None
    )
    assert targeted_entry is not None
    assert len(targeted_entry["results"]) == 1
    assert targeted_entry["results"][0]["title"] == "Acme Corp has 50 employees"
