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
        "targeted_search": [{"claim": "Acme has 50 employees", "results": [{"title": "T", "url": "U", "snippet": "S", "text": "v" * 5000}]}],
    }

    truncated = data_scout_agent._truncate_sources(sources)

    assert truncated["github"] == sources["github"]  # untouched, already bounded at the source
    assert len(truncated["pages"][0]["text"]) < 5000
    assert len(truncated["pages"][0]["subpages"][0]["text"]) < 5000
    assert len(truncated["link_lookups"][0]["results"][0]["text"]) < 5000
    assert len(truncated["blind_search"][0]["results"][0]["text"]) < 5000
    assert len(truncated["targeted_search"][0]["results"][0]["text"]) < 5000
    # Structural fields (non-text) must survive untouched for citation purposes.
    assert truncated["link_lookups"][0]["domain"] == "linkedin.com"
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
async def test_extract_claims_happy_path(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"claims": ["We have 50 employees", "We opened a Boston office in 2024"]})}}]},
        )
    )

    claims = await data_scout_agent._extract_claims("We have 50 employees. We opened a Boston office in 2024.")

    assert claims == ["We have 50 employees", "We opened a Boston office in 2024"]


@respx.mock
async def test_extract_claims_strips_markdown_fences(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    fenced = "```json\n" + json.dumps({"claims": ["A claim"]}) + "\n```"
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": fenced}}]})
    )

    claims = await data_scout_agent._extract_claims("Some transcript text.")

    assert claims == ["A claim"]


async def test_extract_claims_returns_empty_without_api_key(patch_settings):
    patch_settings(gemini_api_key=None)
    assert await data_scout_agent._extract_claims("Some transcript.") == []


async def test_extract_claims_returns_empty_for_blank_transcript(patch_settings):
    patch_settings(gemini_api_key="gem-key")
    assert await data_scout_agent._extract_claims("   ") == []


@respx.mock
async def test_extract_claims_returns_empty_on_http_error(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(return_value=httpx.Response(500))

    assert await data_scout_agent._extract_claims("Some transcript.") == []


@respx.mock
async def test_extract_claims_returns_empty_on_malformed_json(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "not json at all"}}]})
    )

    assert await data_scout_agent._extract_claims("Some transcript.") == []


@respx.mock
async def test_run_scout_gathers_blind_search_queries(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    queries = []

    async def fake_search(query):
        queries.append(query)
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    await data_scout_agent.run_scout("Acme Corp", None, None, None, None)

    assert queries == [
        "Acme Corp overview",
        "Acme Corp latest news",
        "Acme Corp revenue",
        "Acme Corp employees",
        "Acme Corp leadership",
        "Acme Corp reviews",
        "Acme Corp controversies",
        "Acme Corp clients",
    ]
    # Industry-neutral: none of tech-specific wording leaks into the queries.
    assert not any("tech stack" in q or "engineering" in q for q in queries)


@respx.mock
async def test_run_scout_fetches_website_and_subpages(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_fetch_page_with_subpages(url):
        assert url == "https://acme.example.com"
        return {"text": "Acme Corp builds widgets.", "subpages": [{"url": "https://acme.example.com/about", "text": "About Acme"}]}

    async def fake_search(query):
        return []

    monkeypatch.setattr("app.services.page_fetcher.fetch_page_with_subpages", fake_fetch_page_with_subpages)
    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "https://acme.example.com", None, None, None)

    assert result["sources"]["pages"] == [
        {
            "url": "https://acme.example.com",
            "text": "Acme Corp builds widgets.",
            "subpages": [{"url": "https://acme.example.com/about", "text": "About Acme"}],
        }
    ]


@respx.mock
async def test_run_scout_omits_pages_key_when_no_website_given(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query):
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", None, None, None, None)

    assert "pages" not in result["sources"]


@respx.mock
async def test_run_scout_omits_github_key_when_no_github_url_discovered(patch_settings, monkeypatch):
    # The actual bias this guards against: a law firm, restaurant chain, or
    # any non-technical company must never get an empty/negative GitHub
    # section just because it has no GitHub presence.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query):
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": []})}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]}),
        ]
    )

    result = await data_scout_agent.run_scout("Doe & Associates Law Firm", None, None, None, "We handle corporate litigation.")

    assert "github" not in result["sources"]


@respx.mock
async def test_run_scout_gathers_github_when_url_found_in_transcript(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_fetch_profile(username):
        assert username == "acmecorp"
        return {"username": "acmecorp", "public_repos": 3}

    async def fake_search(query):
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
        "Acme Corp", None, None, None, "Our code is open source at https://github.com/acmecorp."
    )

    assert result["sources"]["github"] == [{"username": "acmecorp", "public_repos": 3}]


@respx.mock
async def test_run_scout_gathers_github_when_url_found_on_website(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_fetch_page_with_subpages(url):
        return {"text": "Find our code at https://github.com/acmecorp", "subpages": []}

    async def fake_fetch_profile(username):
        return {"username": username, "public_repos": 1}

    async def fake_search(query):
        return []

    monkeypatch.setattr("app.services.page_fetcher.fetch_page_with_subpages", fake_fetch_page_with_subpages)
    monkeypatch.setattr("app.services.github_client.fetch_profile", fake_fetch_profile)
    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "https://acme.example.com", None, None, None)

    assert result["sources"]["github"] == [{"username": "acmecorp", "public_repos": 1}]


@respx.mock
async def test_run_scout_gathers_link_lookups_for_no_scrape_domains_discovered(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_fetch_page_text(url):
        return "Full LinkedIn page text"

    async def fake_search(query):
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
        "Acme Corp", None, None, None, "Follow us at https://linkedin.com/company/acme"
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


async def test_run_scout_skips_pass_b_entirely_when_no_transcript(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    extract_claims_called = False

    async def fake_extract_claims(transcript):
        nonlocal extract_claims_called
        extract_claims_called = True
        return []

    async def fake_search(query):
        return []

    monkeypatch.setattr(data_scout_agent, "_extract_claims", fake_extract_claims)
    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    with respx.mock:
        respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
        )
        result = await data_scout_agent.run_scout("Acme Corp", None, None, None, None)

    assert extract_claims_called is False
    assert result["interview_claims"] == []
    assert "interview_claims" not in result["sources"]


@respx.mock
async def test_run_scout_gathers_targeted_search_per_claim_when_transcript_given(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    queries = []

    async def fake_search(query):
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
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": ["We have 50 employees"]})}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]}),
        ]
    )

    result = await data_scout_agent.run_scout("Acme Corp", None, None, None, "We have 50 employees.")

    assert result["interview_claims"] == ["We have 50 employees"]
    assert result["sources"]["interview_claims"] == ["We have 50 employees"]
    assert result["sources"]["targeted_search"] == [
        {
            "claim": "We have 50 employees",
            "results": [{"title": "Acme grows", "url": "https://news.example.com/acme-50", "snippet": "Acme now has 50 staff.", "text": None}],
        }
    ]
    assert "Acme Corp We have 50 employees" in queries


@respx.mock
async def test_run_scout_targeted_search_dedupes_against_pass_a_urls(patch_settings, monkeypatch):
    # The exact behavior this guards against: a URL Pass A already surfaced
    # (blind search) must not be re-listed under targeted_search too. The
    # claim still gets an entry (now required for the Data Gaps section),
    # just with an empty results list once the only hit is deduped away.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    shared_hit = {"title": "Acme overview", "url": "https://news.example.com/acme", "snippet": "..."}

    async def fake_search(query):
        if query == "Acme Corp overview":
            return [shared_hit]
        if query.startswith("Acme Corp We have"):
            return [shared_hit]  # same URL as the blind search already found
        return []

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": ["We have 50 employees"]})}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]}),
        ]
    )

    result = await data_scout_agent.run_scout("Acme Corp", None, None, None, "We have 50 employees.")

    assert result["sources"]["targeted_search"] == [{"claim": "We have 50 employees", "results": []}]


async def test_run_scout_soft_fails_when_gemini_key_missing(patch_settings, monkeypatch):
    # run_scout must never raise on a synthesis failure (missing key, quota
    # exhaustion, etc.) - it should return findings_ok=False with whatever
    # sources were gathered, not lose that data by propagating the exception.
    patch_settings(gemini_api_key=None)

    async def fake_search(query):
        return []

    async def fake_fetch_profile(username):
        return {"username": username, "public_repos": 1}

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.github_client.fetch_profile", fake_fetch_profile)

    result = await data_scout_agent.run_scout("Acme Corp", None, None, None, "Code at https://github.com/acmecorp")

    assert result["findings_ok"] is False
    assert result["internet_findings"] == ""
    # Gathered sources must survive even though synthesis failed.
    assert result["sources"]["github"] == [{"username": "acmecorp", "public_repos": 1}]


@respx.mock
async def test_run_scout_degrades_gracefully_when_website_fetch_fails(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_fetch_page_with_subpages(url):
        return None  # simulates page_fetcher's own internal failure handling

    async def fake_search(query):
        return []

    monkeypatch.setattr("app.services.page_fetcher.fetch_page_with_subpages", fake_fetch_page_with_subpages)
    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "https://acme.example.com", None, None, None)

    assert "pages" not in result["sources"]


@respx.mock
async def test_run_scout_degrades_gracefully_when_github_lookup_fails(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_fetch_profile(username):
        return None  # simulates github_client's own internal failure handling

    async def fake_search(query):
        return []

    monkeypatch.setattr("app.services.github_client.fetch_profile", fake_fetch_profile)
    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": []})}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]}),
        ]
    )

    result = await data_scout_agent.run_scout("Acme Corp", None, None, None, "Code at https://github.com/ghost")

    assert "github" not in result["sources"]
    assert result["internet_findings"] == "report"


# --- Speed improvements ---------------------------------------------------


def test_claim_specificity_score_prioritizes_concrete_facts_over_vague_metrics():
    assert data_scout_agent._claim_specificity_score("We were founded in 2019") > 0
    assert data_scout_agent._claim_specificity_score("We have 50 employees") > 0
    assert data_scout_agent._claim_specificity_score("Our retention rate is 95%") == -1
    assert data_scout_agent._claim_specificity_score("Our growth percentage is strong") == -1
    assert data_scout_agent._claim_specificity_score("We care about quality") == 0


def test_select_targeted_claims_returns_all_when_under_cap():
    claims = [f"Claim {i}" for i in range(5)]
    assert data_scout_agent._select_targeted_claims(claims) == claims


def test_select_targeted_claims_caps_at_ten_and_prefers_specific_claims():
    specific = [f"We opened office number {i} in 20{10 + i}" for i in range(10)]
    vague = [f"Our retention rate is great, case {i}" for i in range(5)]
    claims = vague + specific  # vague ones come first in transcript order

    selected = data_scout_agent._select_targeted_claims(claims)

    assert len(selected) == 10
    # All 10 specific (numeric/year-bearing) claims made the cut; the vague
    # retention-rate ones (unlikely to have public data) were dropped first.
    assert set(selected) == set(specific)


def test_select_targeted_claims_preserves_original_order_among_selected():
    # Ties (equal specificity score) keep transcript order - a stable
    # selection, not an arbitrary reshuffle.
    claims = [f"We have {i} offices" for i in range(15)]
    selected = data_scout_agent._select_targeted_claims(claims)
    assert selected == claims[:10]


@respx.mock
async def test_gather_blind_search_runs_all_eight_queries_concurrently(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    delay = 0.05

    async def fake_search(query):
        await asyncio.sleep(delay)
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    start = time.monotonic()
    await data_scout_agent._gather_blind_search("Acme Corp")
    elapsed = time.monotonic() - start

    # Sequential would take ~8 * delay; concurrent should take ~1 * delay.
    assert elapsed < delay * 4


@respx.mock
async def test_gather_targeted_runs_all_claim_queries_concurrently(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    delay = 0.05
    claims = [f"Claim number {i}" for i in range(6)]

    async def fake_search(query):
        await asyncio.sleep(delay)
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    start = time.monotonic()
    await data_scout_agent._gather_targeted("Acme Corp", claims, set())
    elapsed = time.monotonic() - start

    assert elapsed < delay * 3


@respx.mock
async def test_gather_targeted_slices_results_to_three_per_claim(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query):
        return [
            {"title": f"Result {i}", "url": f"https://news.example.com/{i}", "snippet": f"snippet {i}"}
            for i in range(5)
        ]

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)

    targeted = await data_scout_agent._gather_targeted("Acme Corp", ["We have 50 employees"], set())

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

    result = await data_scout_agent.run_scout("Acme Corp", None, None, None, None)

    assert result["internet_findings"] == "report"
    assert result["findings_ok"] is True
    assert result["sources"] == {}  # nothing finished gathering before the deadline


@respx.mock
async def test_synthesize_internet_findings_receives_interview_claims(patch_settings):
    # The new claim-focused report format requires the synthesis call to see
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
