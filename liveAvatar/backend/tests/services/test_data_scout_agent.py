import asyncio
import json
import time

import httpx
import pytest
import respx

from app.config import settings
from app.services import data_scout_agent, scout_gather, scout_utils

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

_DEFAULT_VERIFICATION_JSON = {
    "tied_to_stated_company": False,
    "role": "unknown",
    "background": {"tenure": "not found", "projects": [], "focus_areas": []},
    "summary": "no data",
    "citations": [],
}


def _gemini_dispatch(*, claims_json=None, document_claims_json=None, verification_json=None, synthesis_content="report"):
    """Builds a respx side_effect callable that routes one chat/completions
    request to the right canned response by CONTENT (the response_format's
    json_schema name, or the system prompt's own wording) rather than by
    call order. Needed since 2026-08-01: representative verification's
    extraction call and the main synthesis call are independent, CONCURRENT
    Gemini calls, so a fixed positional side_effect list can no longer
    assume which one lands in which slot (claim extraction, meanwhile, runs
    concurrently with Pass A - which makes no Gemini calls of its own - so
    it remains reliably the first chat/completions call chronologically,
    but is routed by content here too for one consistent dispatcher).

    Interview claim extraction and document claim extraction (see Change 1)
    share the exact same system prompt, so they're distinguished by the USER
    message instead: a document-only extraction call's combined text always
    contains "Attached Document" (see scout_utils._combine_claim_sources),
    which an interview (transcript/extra_context) call's never does."""

    def _dispatch(request):
        body = json.loads(request.content)
        schema_name = (body.get("response_format") or {}).get("json_schema", {}).get("name")
        if schema_name == "representative_verification":
            content = json.dumps(verification_json if verification_json is not None else _DEFAULT_VERIFICATION_JSON)
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        system_content = body["messages"][0]["content"]
        if "Extract a JSON list of EVERY factual statement" in system_content:
            user_message = body["messages"][1]["content"]
            if "Attached Document" in user_message:
                content = json.dumps(document_claims_json if document_claims_json is not None else {"claims": []})
            else:
                content = json.dumps(claims_json if claims_json is not None else {"claims": []})
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": synthesis_content}}]})

    return _dispatch


def _synthesis_request_body(route) -> dict:
    """Finds the synthesis call among a mocked route's captured calls by the
    same content-based rule _gemini_dispatch uses (never by position/order -
    representative verification and synthesis are concurrent, so their
    relative call order isn't guaranteed)."""
    for call in route.calls:
        body = json.loads(call.request.content)
        schema_name = (body.get("response_format") or {}).get("json_schema", {}).get("name")
        if schema_name == "representative_verification":
            continue
        if "Extract a JSON list of EVERY factual statement" in body["messages"][0]["content"]:
            continue
        return body
    raise AssertionError("No synthesis call found among the mocked route's calls")


def test_trunc_leaves_short_text_unchanged():
    assert scout_utils._trunc("short", 100) == "short"


def test_trunc_returns_none_unchanged():
    assert scout_utils._trunc(None, 100) is None


def test_trunc_cuts_long_text_and_notes_omitted_count():
    text = "x" * 150
    result = scout_utils._trunc(text, 100)
    assert result.startswith("x" * 100)
    assert "50 chars omitted" in result


def test_extract_urls_finds_and_dedupes_across_blobs():
    blobs = [
        "Check us out at https://acme.example.com and https://linkedin.com/company/acme.",
        "Same site again: https://acme.example.com (great place).",
    ]
    urls = scout_utils._extract_urls(blobs)
    assert urls == sorted(["https://acme.example.com", "https://linkedin.com/company/acme"])


def test_extract_urls_ignores_blank_blobs():
    assert scout_utils._extract_urls([None, "", "no links here"]) == []


def test_truncate_sources_only_includes_keys_present_in_input():
    # An empty input must produce an empty output - omitted keys must never
    # be synthesized back in as empty lists (that would bias the report
    # toward "no targeted search" sections for every company).
    assert scout_utils._truncate_sources({}) == {}


def test_truncate_sources_caps_page_link_lookup_and_search_text():
    sources = {
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

    truncated = scout_utils._truncate_sources(sources)

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
    truncated = scout_utils._truncate_sources({"interview_claims": ["a claim"]})
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
    website = await scout_gather._derive_company_website(
        "Acme Corp", "Learn more at https://acme.example.com about us.", None
    )
    assert website == "https://acme.example.com"


async def test_derive_company_website_finds_url_in_documents():
    website = await scout_gather._derive_company_website(
        "Acme Corp", None, ["Company profile.", "Our site is https://acme.example.com"]
    )
    assert website == "https://acme.example.com"


@respx.mock
async def test_derive_company_website_falls_back_to_search(monkeypatch):
    async def fake_search(query, **kwargs):
        assert query == "Acme Corp official website"
        return [{"title": "Acme", "url": "https://www.acme.example.com/home", "snippet": "..."}]

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await scout_gather._derive_company_website("Acme Corp", None, None)

    # _domain_of strips a leading "www." by design (consistent with the rest
    # of the module), so the derived website is the bare domain.
    assert website == "https://acme.example.com"


async def test_derive_company_website_returns_none_when_nothing_found(monkeypatch):
    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await scout_gather._derive_company_website("Acme Corp", None, None)

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

    website = await scout_gather._derive_company_website("Acme Corp", None, None)

    assert website == "https://acme.example.com"


async def test_derive_company_website_returns_none_when_every_hit_excluded(monkeypatch):
    async def fake_search(query, **kwargs):
        return [
            {"title": "Acme Corp - LinkedIn", "url": "https://www.linkedin.com/company/acme", "snippet": "..."},
            {"title": "Acme Corp (@acme) - X", "url": "https://twitter.com/acme", "snippet": "..."},
        ]

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await scout_gather._derive_company_website("Acme Corp", None, None)

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

    website = await scout_gather._derive_company_website("Acme Corp", None, None)

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

    website = await scout_gather._derive_company_website("Zensar Technologies", None, None)

    assert website == "https://zensar.com"


async def test_derive_company_website_returns_none_when_every_hit_excluded_including_reference_sites(monkeypatch):
    async def fake_search(query, **kwargs):
        return [
            {"title": "Acme Corp - LinkedIn", "url": "https://www.linkedin.com/company/acme", "snippet": "..."},
            {"title": "Acme Corp - Wikipedia", "url": "https://en.wikipedia.org/wiki/Acme_Corp", "snippet": "..."},
        ]

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await scout_gather._derive_company_website("Acme Corp", None, None)

    assert website is None


async def test_derive_company_website_skips_wikipedia_url_mentioned_in_extra_context():
    # A literal Wikipedia URL mentioned in extra_context/documents must not
    # be treated as the company's own site either - same exclusion applies
    # to the text-mentioned-URL path, not just the search fallback.
    website = await scout_gather._derive_company_website(
        "Zensar Technologies",
        "See https://en.wikipedia.org/wiki/Zensar_Technologies and https://www.zensar.com for more.",
        None,
    )
    assert website == "https://www.zensar.com"


async def test_derive_company_website_skips_unrelated_non_excluded_top_hit(monkeypatch):
    # Real case found in testing: an entirely unrelated organization
    # (uac.org.au, an Australian admissions body) isn't on any exclusion
    # blocklist, so only the relevance check catches it - it shares no
    # token with the company name at all, unlike the two prior
    # exclusion-based bugs (LinkedIn/Wikipedia).
    async def fake_search(query, **kwargs):
        return [
            {"title": "UAC - University Admissions Centre", "url": "https://uac.org.au/apply", "snippet": "Apply for undergraduate courses in Australia."},
            {"title": "Newgen Software - Official Site", "url": "https://www.newgensoft.com/home", "snippet": "Newgen Software Technologies provides BPM, ECM and low-code solutions."},
        ]

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await scout_gather._derive_company_website("Newgen Software Technologies Limited", None, None)

    assert website == "https://newgensoft.com"


async def test_derive_company_website_returns_none_when_every_hit_irrelevant(monkeypatch):
    # None of the hits are on the exclusion blocklist, but none of them
    # share any token with company_name either - must not fall back to
    # accepting one anyway.
    async def fake_search(query, **kwargs):
        return [
            {"title": "Random Blog", "url": "https://randomblog.example/post", "snippet": "Nothing to do with the search subject."},
            {"title": "Widget News", "url": "https://widgetnews.example/story", "snippet": "Also unrelated."},
        ]

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await scout_gather._derive_company_website("Acme Corp", None, None)

    assert website is None


async def test_derive_company_website_exclusion_list_still_wins_even_if_relevant(monkeypatch):
    # A hit can mention the company name and still be correctly rejected
    # via the exclusion list - relevance passing is not enough to override
    # an excluded domain (LinkedIn here), same as before this fix.
    async def fake_search(query, **kwargs):
        return [
            {"title": "Acme Corp - LinkedIn", "url": "https://www.linkedin.com/company/acme", "snippet": "Acme Corp official company page"},
            {"title": "Acme", "url": "https://www.acme.example.com/home", "snippet": "..."},
        ]

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await scout_gather._derive_company_website("Acme Corp", None, None)

    assert website == "https://acme.example.com"


async def test_derive_company_website_search_fallback_accepts_relevant_non_excluded_top_hit(monkeypatch):
    # Happy path: a top hit that is both non-excluded and relevant is still
    # accepted immediately - no regression from adding the relevance check.
    async def fake_search(query, **kwargs):
        return [{"title": "Acme Corp - Official Site", "url": "https://www.acme.example.com/home", "snippet": "Acme Corp builds widgets."}]

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await scout_gather._derive_company_website("Acme Corp", None, None)

    assert website == "https://acme.example.com"


async def test_derive_company_website_prefers_domain_matching_hit_over_relevant_third_party_hit(monkeypatch):
    # Real case found in testing: IBM's own PartnerPlus directory hosts a
    # page genuinely ABOUT Persistent Systems (passes relevance - title
    # literally says "PERSISTENT SYSTEMS LIMITED") on ibm.com, which isn't
    # excluded either - both existing safeguards accept it legitimately.
    # The domain-preference tiebreaker must still prefer the hit whose own
    # domain matches the company name over this relevant-but-third-party one,
    # even though ibm.com ranks first.
    async def fake_search(query, **kwargs):
        return [
            {"title": "PERSISTENT SYSTEMS LIMITED", "url": "https://www.ibm.com/partnerplus/directory/company/2552", "snippet": "Persistent Systems is a global services and solutions company."},
            {"title": "Persistent Systems - Official Site", "url": "https://www.persistent.com/", "snippet": "Persistent Systems delivers digital engineering."},
        ]

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await scout_gather._derive_company_website("Persistent Systems", None, None)

    assert website == "https://persistent.com"


async def test_derive_company_website_falls_back_to_relevant_hit_when_no_domain_matches(monkeypatch):
    # If nothing in the results has a domain matching the company name,
    # degrading gracefully to the first relevant (ACCEPTABLE) hit is more
    # important than returning None - a relevant third-party page is still
    # useful, better than nothing.
    async def fake_search(query, **kwargs):
        return [
            {"title": "PERSISTENT SYSTEMS LIMITED", "url": "https://www.ibm.com/partnerplus/directory/company/2552", "snippet": "Persistent Systems is a global services and solutions company."},
            {"title": "Persistent Systems - Crunchbase", "url": "https://www.crunchbase.com/organization/persistent-systems", "snippet": "Persistent Systems company profile."},
        ]

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await scout_gather._derive_company_website("Persistent Systems", None, None)

    assert website == "https://ibm.com"


async def test_derive_company_website_returns_immediately_when_top_hit_matches_domain(monkeypatch):
    # Happy path: when the very first hit already passes exclusion +
    # relevance + domain match, it's returned immediately - no regression
    # from adding the domain-preference tiebreaker.
    async def fake_search(query, **kwargs):
        return [
            {"title": "Persistent Systems - Official Site", "url": "https://www.persistent.com/", "snippet": "Persistent Systems delivers digital engineering."},
            {"title": "PERSISTENT SYSTEMS LIMITED", "url": "https://www.ibm.com/partnerplus/directory/company/2552", "snippet": "Persistent Systems is a global services and solutions company."},
        ]

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await scout_gather._derive_company_website("Persistent Systems", None, None)

    assert website == "https://persistent.com"


async def test_derive_company_website_domain_preference_does_not_override_exclusion(patch_settings, monkeypatch):
    # A hit whose domain happens to contain a company-name token is still
    # correctly rejected if that domain is on the exclusion list - the
    # domain-preference signal only applies among hits that already passed
    # both the exclusion and relevance checks; it never rescues an excluded
    # one just because its domain would otherwise be PREFERRED-tier.
    patch_settings(scout_reference_site_domains=("acme-wiki.example",))

    async def fake_search(query, **kwargs):
        return [
            {"title": "Acme Corp - Acme Wiki", "url": "https://acme-wiki.example/acme-corp", "snippet": "Acme Corp encyclopedia entry"},
            {"title": "Acme Corp - Official Site", "url": "https://www.acme.example.com/home", "snippet": "Acme Corp builds widgets."},
        ]

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await scout_gather._derive_company_website("Acme Corp", None, None)

    assert website == "https://acme.example.com"


def test_normalize_company_tokens_strips_limited_not_just_ltd():
    assert scout_utils._normalize_company_tokens("Newgen Software Technologies Limited") == {
        "newgen", "software", "technologies",
    }


# --- Representative search --------------------------------------------------


async def test_gather_representative_search_runs_query_and_returns_entry(monkeypatch):
    async def fake_search(query, **kwargs):
        assert query == "Jane Doe Acme Corp"
        return [{"title": "Jane Doe - LinkedIn", "url": "https://linkedin.com/in/janedoe", "snippet": "VP Sales"}]

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)

    result = await scout_gather._gather_representative_search("Jane Doe", "Acme Corp")

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

    assert await scout_gather._gather_representative_search("Jane Doe", "Acme Corp") == []


async def test_gather_representative_search_returns_empty_without_name():
    assert await scout_gather._gather_representative_search("", "Acme Corp") == []


# --- Aramco relationship search (Pass A) ------------------------------------


async def test_gather_relationship_search_runs_query_and_returns_entry(monkeypatch):
    async def fake_search(query, **kwargs):
        assert query == "Acme Corp Saudi Aramco"
        return [{"title": "Acme Corp - Aramco partnership", "url": "https://news.example.com/acme-aramco", "snippet": "Acme signed a deal with Saudi Aramco."}]

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)

    result = await scout_gather._gather_relationship_search("Acme Corp")

    assert result == [
        {
            "query": "Acme Corp Saudi Aramco",
            "results": [
                {
                    "title": "Acme Corp - Aramco partnership",
                    "url": "https://news.example.com/acme-aramco",
                    "snippet": "Acme signed a deal with Saudi Aramco.",
                    "text": None,
                }
            ],
        }
    ]


async def test_gather_relationship_search_returns_empty_without_hits(monkeypatch):
    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    assert await scout_gather._gather_relationship_search("Acme Corp") == []


async def test_gather_relationship_search_does_not_apply_recency_bias(monkeypatch):
    # Unlike revenue/employees/leadership blind-search queries, an old Aramco
    # partnership is still relevant - no time_range should be passed.
    seen_time_range = "not called"

    async def fake_search(query, time_range=None, company_name=""):
        nonlocal seen_time_range
        seen_time_range = time_range
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    await scout_gather._gather_relationship_search("Acme Corp")

    assert seen_time_range is None


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

    claims = await scout_gather._extract_claims("We have 50 employees. We opened a Boston office in 2024.", None)

    assert [c["claim"] for c in claims] == ["We have 50 employees", "We opened a Boston office in 2024"]
    assert claims[0]["search_query"] == "50 employees"
    assert claims[0]["source"] == "transcript"


@respx.mock
async def test_extract_claims_combines_transcript_and_context_but_not_documents(patch_settings):
    # Change 1: documents are extracted separately by _extract_document_claims
    # now, so _extract_claims (interview claim extraction) must combine only
    # transcript + extra_context - a document passed in here must NOT appear
    # in the extraction payload, even though the old signature accepted one.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": []})}}]})
    )

    await scout_gather._extract_claims("We have 50 employees.", "We were founded in 2010.")

    sent_body = json.loads(route.calls[0].request.content)
    user_message = sent_body["messages"][1]["content"]
    assert "Interview Transcript" in user_message
    assert "We have 50 employees." in user_message
    assert "Additional Context" in user_message
    assert "We were founded in 2010." in user_message
    assert "Attached Document" not in user_message


@respx.mock
async def test_extract_document_claims_uses_only_document_text(patch_settings):
    # Change 1: _extract_document_claims must extract from documents ONLY -
    # never transcript or extra_context (it doesn't even accept them).
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": []})}}]})
    )

    await scout_gather._extract_document_claims(["We are ISO 9001 certified."])

    sent_body = json.loads(route.calls[0].request.content)
    user_message = sent_body["messages"][1]["content"]
    assert "Attached Document 1" in user_message
    assert "We are ISO 9001 certified." in user_message
    assert "Interview Transcript" not in user_message
    assert "Additional Context" not in user_message


@respx.mock
async def test_extract_document_claims_returns_claims_separately_from_interview_claims(patch_settings):
    # End-to-end check (via _gather_all) that document claims never get
    # mixed into state.claims/state.claim_details (interview claims).
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL, tavily_api_key=None)

    def _dispatch(request):
        body = json.loads(request.content)
        user_message = body["messages"][1]["content"]
        if "Attached Document" in user_message:
            content = json.dumps({"claims": [{"claim": "ISO 9001 certified", "category": "specific", "search_query": "ISO 9001 certified", "source": "document"}]})
        else:
            content = json.dumps({"claims": [{"claim": "50 employees", "category": "specific", "search_query": "50 employees", "source": "transcript"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(side_effect=_dispatch)

    state = scout_gather._GatherState()
    await scout_gather._gather_all(state, "Acme", "", None, ["We are ISO 9001 certified."], "We have 50 employees.")

    assert state.claims == ["50 employees"]
    assert state.document_claims == ["ISO 9001 certified"]
    assert [d["claim"] for d in state.claim_details] == ["50 employees"]
    assert [d["claim"] for d in state.document_claim_details] == ["ISO 9001 certified"]


async def test_extract_document_claims_returns_empty_for_no_documents(patch_settings):
    patch_settings(gemini_api_key="gem-key")
    assert await scout_gather._extract_document_claims(None) == []
    assert await scout_gather._extract_document_claims([]) == []
    assert await scout_gather._extract_document_claims(["   ", ""]) == []


@respx.mock
async def test_extract_document_claims_redacts_pii_before_gemini_call(patch_settings):
    # Fixes the known document PII gap: document text must be redacted
    # before it ever reaches the extraction Gemini call.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": []})}}]})
    )

    await scout_gather._extract_document_claims(["Contact our CEO at jane.doe@example.com or (555) 123-4567."])

    sent_body = json.loads(route.calls[0].request.content)
    user_message = sent_body["messages"][1]["content"]
    assert "jane.doe@example.com" not in user_message
    assert "(555) 123-4567" not in user_message
    assert "[redacted]" in user_message


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

    claims = await scout_gather._extract_claims(None, "We are ISO certified.")

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

    claims = await scout_gather._extract_claims("Some transcript.", None)

    assert claims == [
        {"claim": "A bare claim", "category": "specific", "search_query": "A bare claim", "source": "transcript"}
    ]


@respx.mock
async def test_extract_claims_strips_markdown_fences(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    fenced = "```json\n" + json.dumps({"claims": [{"claim": "A claim", "search_query": "a claim", "source": "transcript"}]}) + "\n```"
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": fenced}}]})
    )

    claims = await scout_gather._extract_claims("Some transcript text.", None)

    assert [c["claim"] for c in claims] == ["A claim"]


async def test_extract_claims_returns_empty_without_api_key(patch_settings):
    patch_settings(gemini_api_key=None)
    assert await scout_gather._extract_claims("Some transcript.", None) == []


async def test_extract_claims_returns_empty_when_all_inputs_blank(patch_settings):
    patch_settings(gemini_api_key="gem-key")
    assert await scout_gather._extract_claims("   ", "  ") == []


@respx.mock
async def test_extract_claims_returns_empty_on_http_error(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(return_value=httpx.Response(500))

    assert await scout_gather._extract_claims("Some transcript.", None) == []


@respx.mock
async def test_extract_claims_returns_empty_on_malformed_json(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "not json at all"}}]})
    )

    assert await scout_gather._extract_claims("Some transcript.", None) == []


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

    await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

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
        "Acme Corp", "Jane Doe", "Visit us at https://acme.example.com", None, None
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
        "Acme Corp", "Jane Doe", None, ["Our company profile: https://acme.example.com"], None
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

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

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

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

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

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

    assert result["sources"]["representative_search"] == [
        {
            "query": "Jane Doe Acme Corp",
            "results": [{"title": "Jane Doe - LinkedIn", "url": "https://linkedin.com/in/janedoe", "snippet": "VP Sales at Acme", "text": None}],
        }
    ]
    # Representative Identity Verification: the structured result is
    # returned, and its rendered markdown is prepended ahead of the
    # synthesized report (the mocked Gemini response "report" isn't valid
    # JSON, so the verification LLM call itself soft-fails to NO_DATA - a
    # technical failure, distinct from NOT_FOUND - dedicated VERIFIED/
    # NOT_FOUND/NO_DATA scenarios live in test_representative_verification.py).
    assert result["representative_verification"] is not None
    assert result["representative_verification"].verdict == "NO_DATA"
    assert result["internet_findings"].startswith("## Representative Verification")
    assert result["internet_findings"].endswith("report")


@respx.mock
async def test_run_scout_gathers_relationship_search_and_flows_to_synthesis_payload(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        if query == "Acme Corp Saudi Aramco":
            return [{"title": "Acme Corp - Aramco partnership", "url": "https://news.example.com/acme-aramco", "snippet": "Acme signed a deal with Saudi Aramco."}]
        return []

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    route = respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

    assert result["sources"]["relationship_search"] == [
        {
            "query": "Acme Corp Saudi Aramco",
            "results": [
                {
                    "title": "Acme Corp - Aramco partnership",
                    "url": "https://news.example.com/acme-aramco",
                    "snippet": "Acme signed a deal with Saudi Aramco.",
                    "text": None,
                }
            ],
        }
    ]
    # Confirm it actually reaches the synthesis call's payload, not just state.
    synthesis_user_message = json.loads(route.calls[0].request.content)["messages"][1]["content"]
    assert "relationship_search" in synthesis_user_message
    assert "Saudi Aramco" in synthesis_user_message


@respx.mock
async def test_run_scout_completes_normally_when_relationship_search_returns_nothing(patch_settings, monkeypatch):
    # Graceful degradation: Tavily returning nothing for the Aramco query
    # must never block or fail the rest of the Scout run.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

    assert "relationship_search" not in result["sources"]
    # The Representative Verification section is prepended ahead of the
    # synthesized report - assert the synthesis text survives at the end.
    assert result["internet_findings"].endswith("report")
    assert result["findings_ok"] is True


@respx.mock
async def test_run_scout_relationship_search_applies_relevance_filter(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        if query == "Acme Corp Saudi Aramco":
            return [
                {"title": "Acme Corp signs Aramco deal", "url": "https://news.example.com/acme-aramco", "snippet": "Acme Corp partnership."},
                {"title": "Other Company wins award", "url": "https://news.example.com/other", "snippet": "Recognized for community service."},
            ]
        return []

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

    urls = [r["url"] for r in result["sources"]["relationship_search"][0]["results"]]
    assert "https://news.example.com/acme-aramco" in urls
    assert "https://news.example.com/other" not in urls


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
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(side_effect=_gemini_dispatch())

    result = await data_scout_agent.run_scout(
        "Acme Corp", "Jane Doe", "Follow us at https://linkedin.com/company/acme", None, None
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

    async def fake_extract_claims(transcript, extra_context):
        nonlocal extract_claims_called
        extract_claims_called = True
        return []

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr(scout_gather, "_extract_claims", fake_extract_claims)
    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    with respx.mock:
        respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
        )
        result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

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
        side_effect=_gemini_dispatch(
            claims_json={"claims": [{"claim": "We have 50 employees", "search_query": "Acme Corp 50 employees", "source": "transcript"}]}
        )
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, "We have 50 employees.")

    assert result["interview_claims"] == ["We have 50 employees"]
    # The synthesis payload carries the extraction's own category/source tags
    # so the model never re-derives SPECIFIC vs GENERAL from targeted_search
    # presence; the top-level interview_claims return stays plain strings.
    assert result["sources"]["interview_claims"] == [
        {"claim": "We have 50 employees", "category": "specific", "source": "transcript"}
    ]
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
        side_effect=_gemini_dispatch(
            claims_json={"claims": [{"claim": "We were founded in 2010", "search_query": "Acme Corp founded 2010", "source": "context"}]}
        )
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", "We were founded in 2010.", None, None)

    assert result["interview_claims"] == ["We were founded in 2010"]


# --- Document Claims: extracted/verified separately from Interview Claims
# (Change 1/2/3) ---------------------------------------------------------


@respx.mock
async def test_run_scout_document_claims_kept_separate_from_interview_claims(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        if "50 employees" in query:
            return [{"title": "Acme grows", "url": "https://news.example.com/acme-50", "snippet": "Acme now has 50 staff."}]
        if "ISO 9001" in query:
            return [{"title": "Acme cert registry", "url": "https://certs.example.com/acme-iso", "snippet": "Acme holds ISO 9001."}]
        return []

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=_gemini_dispatch(
            claims_json={"claims": [{"claim": "We have 50 employees", "search_query": "Acme Corp 50 employees", "source": "transcript"}]},
            document_claims_json={"claims": [{"claim": "We are ISO 9001 certified", "search_query": "Acme Corp ISO 9001", "source": "document"}]},
        )
    )

    result = await data_scout_agent.run_scout(
        "Acme Corp", "Jane Doe", None, ["We are ISO 9001 certified."], "We have 50 employees."
    )

    # Interview claims/search stay exactly as before - untouched by documents.
    assert result["interview_claims"] == ["We have 50 employees"]
    assert result["sources"]["interview_claims"] == [
        {"claim": "We have 50 employees", "category": "specific", "source": "transcript"}
    ]
    assert result["sources"]["targeted_search"] == [
        {
            "claim": "We have 50 employees",
            "results": [{"title": "Acme grows", "url": "https://news.example.com/acme-50", "snippet": "Acme now has 50 staff.", "text": None}],
        }
    ]
    # Document claims/search are a completely separate key - never merged in.
    assert result["sources"]["document_claims"] == [
        {"claim": "We are ISO 9001 certified", "category": "specific", "source": "document"}
    ]
    assert result["sources"]["document_targeted_search"] == [
        {
            "claim": "We are ISO 9001 certified",
            "results": [{"title": "Acme cert registry", "url": "https://certs.example.com/acme-iso", "snippet": "Acme holds ISO 9001.", "text": None}],
        }
    ]
    assert result["sources"]["documents"] == ["We are ISO 9001 certified."]


async def test_run_scout_omits_document_keys_when_no_documents_given(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    with respx.mock:
        respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(side_effect=_gemini_dispatch())
        result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, "We have 50 employees.")

    assert "document_claims" not in result["sources"]
    assert "document_targeted_search" not in result["sources"]
    assert "documents" not in result["sources"]


@respx.mock
async def test_run_scout_synthesis_payload_includes_document_data(patch_settings, monkeypatch):
    # Confirms the new sources keys actually reach the Gemini synthesis
    # payload (scout_utils._truncate_sources doesn't recognize them, so
    # data_scout_agent._synthesize_internet_findings must carry them over
    # itself - see Change 2/3).
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    route = respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=_gemini_dispatch(
            document_claims_json={"claims": [{"claim": "We are ISO 9001 certified", "search_query": "ISO 9001", "source": "document"}]},
        )
    )

    await data_scout_agent.run_scout(
        "Acme Corp", "Jane Doe", None, ["We are ISO 9001 certified."], "We have 50 employees."
    )

    synthesis_body = _synthesis_request_body(route)
    payload_json = synthesis_body["messages"][1]["content"]
    assert "We are ISO 9001 certified" in payload_json
    assert '"document_claims"' in payload_json
    assert '"documents"' in payload_json


@respx.mock
async def test_run_scout_redacts_document_text_before_synthesis_payload(patch_settings, monkeypatch):
    # Change 3: _redact_pii must be applied to document text before any
    # Gemini call - including the raw text carried into the synthesis
    # payload for the DOCUMENT CONTRADICTION RULE check, not just the
    # document claim-extraction call.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    route = respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(side_effect=_gemini_dispatch())

    await data_scout_agent.run_scout(
        "Acme Corp", "Jane Doe", None, ["Contact our CEO at jane.doe@example.com or (555) 123-4567."], None
    )

    synthesis_body = _synthesis_request_body(route)
    payload_json = synthesis_body["messages"][1]["content"]
    assert "jane.doe@example.com" not in payload_json
    assert "(555) 123-4567" not in payload_json
    assert "[redacted]" in payload_json


@respx.mock
async def test_run_scout_preserves_interview_order_despite_mixed_verdicts_and_scrambled_extraction_order(
    patch_settings, monkeypatch
):
    # Regression test for the 2026-08-02 bug: claims must appear in the
    # report in the order they were STATED in the interview, never
    # reordered by verdict (CONTRADICTED/PARTIAL/VERIFIED/NO DATA) or by
    # claim type. The extraction step is mocked returning claims in a
    # SCRAMBLED array order (an LLM's raw JSON array order isn't guaranteed
    # to match the order it read the text in) but tagged with explicit
    # "position" values reflecting the TRUE interview order -
    # scout_utils._parse_claims must recover that true order regardless of
    # the scrambled array, and that corrected order must be what reaches the
    # synthesis payload (the only place claim status labels are decided).
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    route = respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=_gemini_dispatch(
            claims_json={
                "claims": [
                    {
                        "claim": "Revenue over $1 billion (stated third)",
                        "category": "specific",
                        "search_query": "revenue 1 billion",
                        "source": "transcript",
                        "position": 2,
                    },
                    {
                        "claim": "Founded in 2010 (stated first)",
                        "category": "specific",
                        "search_query": "founded 2010",
                        "source": "transcript",
                        "position": 0,
                    },
                    {
                        "claim": "Global presence (stated second)",
                        "category": "general",
                        "search_query": None,
                        "source": "transcript",
                        "position": 1,
                    },
                ]
            }
        )
    )

    result = await data_scout_agent.run_scout(
        "Acme Corp", "Jane Doe", None, None,
        "We were founded in 2010. We have a global presence. Our revenue is over $1 billion.",
    )

    expected_order = [
        "Founded in 2010 (stated first)",
        "Global presence (stated second)",
        "Revenue over $1 billion (stated third)",
    ]
    assert result["interview_claims"] == expected_order
    assert [c["claim"] for c in result["sources"]["interview_claims"]] == expected_order

    # The actual payload sent to the synthesis call must list them in this
    # same interview order too - not the scrambled extraction-array order -
    # since that payload is all the synthesis model ever sees.
    synthesis_body = _synthesis_request_body(route)
    user_message = synthesis_body["messages"][1]["content"]
    positions_in_payload = [user_message.index(claim) for claim in expected_order]
    assert positions_in_payload == sorted(positions_in_payload)


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
        side_effect=_gemini_dispatch(
            claims_json={"claims": [{"claim": "We have 50 employees", "search_query": "Acme Corp 50 employees", "source": "transcript"}]}
        )
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, "We have 50 employees.")

    assert result["sources"]["targeted_search"] == [{"claim": "We have 50 employees", "results": []}]


async def test_run_scout_soft_fails_when_gemini_key_missing(patch_settings, monkeypatch):
    # run_scout must never raise on a synthesis failure (missing key, quota
    # exhaustion, etc.) - it should return findings_ok=False with whatever
    # sources were gathered, not lose that data by propagating the exception.
    patch_settings(gemini_api_key=None)

    async def fake_search(query, **kwargs):
        if query == "Acme Corp overview":
            return [{"title": "Acme Corp overview", "url": "https://news.example.com/acme", "snippet": "..."}]
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

    assert result["findings_ok"] is False
    # No synthesized report text (missing key) - but the independently
    # computed Representative Verification section still renders.
    assert result["internet_findings"].startswith("## Representative Verification")
    # Gathered sources must survive even though synthesis failed.
    assert result["sources"]["blind_search"][0]["query"] == "Acme Corp overview"


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
        "Acme Corp", "Jane Doe", "Visit us at https://acme.example.com", None, None
    )

    assert "pages" not in result["sources"]


# --- Speed improvements ---------------------------------------------------


def test_claim_specificity_score_prioritizes_concrete_facts_over_vague_metrics():
    assert scout_utils._claim_specificity_score("We were founded in 2019") > 0
    assert scout_utils._claim_specificity_score("We have 50 employees") > 0
    assert scout_utils._claim_specificity_score("Our retention rate is 95%") == -1
    assert scout_utils._claim_specificity_score("Our growth percentage is strong") == -1
    assert scout_utils._claim_specificity_score("We care about quality") == 0


def _claim_obj(text):
    return {"claim": text, "search_query": text, "source": "transcript"}


def test_select_targeted_claims_returns_all_when_under_cap():
    claims = [_claim_obj(f"Claim {i}") for i in range(5)]
    assert scout_utils._select_targeted_claims(claims) == claims


def test_select_targeted_claims_caps_at_ten_and_prefers_specific_claims():
    specific = [_claim_obj(f"We opened office number {i} in 20{10 + i}") for i in range(10)]
    vague = [_claim_obj(f"Our retention rate is great, case {i}") for i in range(5)]
    claims = vague + specific  # vague ones come first in extraction order

    selected = scout_utils._select_targeted_claims(claims)

    assert len(selected) == 10
    # All 10 specific (numeric/year-bearing) claims made the cut; the vague
    # retention-rate ones (unlikely to have public data) were dropped first.
    assert {c["claim"] for c in selected} == {c["claim"] for c in specific}


def test_select_targeted_claims_preserves_original_order_among_selected():
    # Ties (equal specificity score) keep extraction order - a stable
    # selection, not an arbitrary reshuffle.
    claims = [_claim_obj(f"We have {i} offices") for i in range(15)]
    selected = scout_utils._select_targeted_claims(claims)
    assert selected == claims[:10]


@respx.mock
async def test_gather_blind_search_runs_all_eight_queries_concurrently(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    delay = 0.05

    async def fake_search(query, **kwargs):
        await asyncio.sleep(delay)
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    state = scout_gather._GatherState()
    start = time.monotonic()
    await scout_gather._fill_blind_search(state, "Acme Corp")
    elapsed = time.monotonic() - start

    # Sequential would take ~8 * delay; concurrent should take ~1 * delay.
    assert elapsed < delay * 4


@respx.mock
async def test_run_pass_a_runs_relationship_search_concurrently_with_blind_search(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    delay = 0.05

    async def fake_search(query, **kwargs):
        await asyncio.sleep(delay)
        if query == "Acme Corp Saudi Aramco":
            return [{"title": "Acme Corp - Aramco partnership", "url": "https://news.example.com/acme-aramco", "snippet": "Acme signed a deal with Saudi Aramco."}]
        return []

    async def fake_fetch_page_text(url):
        return None

    async def fake_fetch_page_with_subpages(url):
        return {"text": "Acme Corp site text", "subpages": []}

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_with_subpages", fake_fetch_page_with_subpages)

    state = scout_gather._GatherState()
    start = time.monotonic()
    # extra_context carries a literal URL so _derive_company_website resolves
    # without its own search call, isolating timing to the concurrent gather.
    await scout_gather._run_pass_a(state, "Acme Corp", "Jane Doe", "Visit us at https://acme.example.com", None)
    elapsed = time.monotonic() - start

    # 8 blind-search queries + representative_search + relationship_search all fire
    # concurrently - sequential would take ~10 * delay, concurrent ~1 * delay.
    assert elapsed < delay * 4
    assert state.relationship_search == [
        {
            "query": "Acme Corp Saudi Aramco",
            "results": [
                {
                    "title": "Acme Corp - Aramco partnership",
                    "url": "https://news.example.com/acme-aramco",
                    "snippet": "Acme signed a deal with Saudi Aramco.",
                    "text": None,
                }
            ],
        }
    ]


@respx.mock
async def test_gather_blind_search_biases_only_revenue_employees_leadership_queries(
    patch_settings, monkeypatch
):
    patch_settings(
        gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL, scout_recency_bias_time_range="y"
    )
    calls: list[tuple[str, str | None]] = []

    async def fake_search(query, time_range=None, company_name=""):
        calls.append((query, time_range))
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    await scout_gather._fill_blind_search(scout_gather._GatherState(), "Acme Corp")

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
    await scout_gather._gather_targeted(claims, set(), "Acme Corp")
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

    targeted = await scout_gather._gather_targeted([_claim_obj("We have 50 employees")], set(), "Acme Corp")

    assert len(targeted[0]["results"]) == 3


@respx.mock
async def test_run_scout_gathering_timeout_proceeds_with_partial_data(patch_settings, monkeypatch):
    # Simulates Pass A taking longer than the gather deadline - run_scout
    # must still complete (proceeding to synthesis) rather than hang or raise.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    monkeypatch.setattr(data_scout_agent, "_GATHER_TIMEOUT_SECONDS", 0.05)

    async def slow_search(query, **kwargs):
        await asyncio.sleep(1.0)
        return []

    monkeypatch.setattr("app.services.web_search_client.search", slow_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

    assert result["internet_findings"].endswith("report")
    assert result["findings_ok"] is True
    assert result["sources"] == {}  # nothing finished gathering before the deadline


# --- PII redaction (_redact_pii) --------------------------------------------


def test_redact_pii_masks_phone_numbers():
    text = "Reach the office at (555) 123-4567 or 555.987.6543 for details."
    redacted = scout_utils._redact_pii(text)
    assert "555" not in redacted
    assert "[redacted]" in redacted


def test_redact_pii_masks_personal_email_but_allows_generic_business_email():
    text = "Contact John Smith directly at john.smith@acme.example, or reach us at info@acme.example."
    redacted = scout_utils._redact_pii(text)
    assert "john.smith@acme.example" not in redacted
    assert "info@acme.example" in redacted


def test_redact_pii_masks_street_address():
    text = "Our headquarters is at 123 Main Street, Suite 400."
    redacted = scout_utils._redact_pii(text)
    assert "123 Main Street" not in redacted
    assert "[redacted]" in redacted


def test_redact_pii_leaves_clean_business_text_unchanged():
    text = "Acme Corp builds ML pipelines for banks and has 200 enterprise customers."
    assert scout_utils._redact_pii(text) == text


def test_redact_pii_returns_none_unchanged():
    assert scout_utils._redact_pii(None) is None


def test_redact_pii_disabled_via_settings_returns_text_unchanged(patch_settings):
    patch_settings(scout_redact_pii=False)
    text = "Call me directly at 555-123-4567 or john.smith@acme.example."
    assert scout_utils._redact_pii(text) == text


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

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

    entry = result["sources"]["blind_search"][0]
    assert "555-123-4567" not in entry["results"][0]["snippet"]
    assert "jane.doe@acme.example" not in entry["results"][0]["text"]


@respx.mock
async def test_run_scout_redacts_pii_from_relationship_search_results(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        if query == "Acme Corp Saudi Aramco":
            return [
                {
                    "title": "Acme Corp - Aramco partnership",
                    "url": "https://news.example.com/acme-aramco",
                    "snippet": "Contact the deal lead at 555-123-4567.",
                }
            ]
        return []

    async def fake_fetch_page_text(url):
        return "Reach out to jane.doe@acme.example for details on the Aramco contract."

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

    entry = result["sources"]["relationship_search"][0]
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
        "Acme Corp", "Jane Doe", "Visit us at https://acme.example.com", None, None
    )

    page = result["sources"]["pages"][0]
    assert "555" not in page["text"]
    assert "jane.doe@acme.example" not in page["subpages"][0]["text"]


# --- Same-company relevance filter (_filter_relevant / _is_relevant_result) --


def test_normalize_company_tokens_strips_suffix_and_punctuation():
    assert scout_utils._normalize_company_tokens("Acme Corp, Inc.") == {"acme"}


def test_normalize_company_tokens_returns_empty_set_for_blank_name():
    assert scout_utils._normalize_company_tokens("") == set()
    assert scout_utils._normalize_company_tokens(None) == set()


def test_is_relevant_result_keeps_result_mentioning_company_name():
    result = {"title": "Acme Corp raises funding", "url": "https://news.example.com/acme", "snippet": "..."}
    assert scout_utils._is_relevant_result(result, {"acme"}) is True


def test_is_relevant_result_drops_result_with_no_name_overlap_anywhere():
    result = {"title": "Random Widgets Co. news", "url": "https://news.example.com/widgets", "snippet": "unrelated story"}
    assert scout_utils._is_relevant_result(result, {"acme"}) is False


def test_is_relevant_result_matches_via_domain_when_title_snippet_dont_mention_name():
    result = {"title": "Latest updates", "url": "https://acmecorp.example.com/news", "snippet": "..."}
    assert scout_utils._is_relevant_result(result, {"acmecorp"}) is True


def test_is_relevant_result_extra_tokens_rescue_a_result_missing_company_name():
    # Representative search: a legitimate LinkedIn hit that only mentions
    # the person's name, not the company, must not be dropped.
    result = {"title": "Jane Doe - LinkedIn", "url": "https://linkedin.com/in/janedoe", "snippet": "VP Sales"}
    assert scout_utils._is_relevant_result(result, {"acme"}, {"jane", "doe"}) is True


def test_is_relevant_result_keeps_everything_when_no_tokens_given():
    result = {"title": "Anything at all", "url": "https://example.com/x", "snippet": "..."}
    assert scout_utils._is_relevant_result(result, set()) is True


def test_filter_relevant_drops_unrelated_results_keeps_matching_ones():
    results = [
        {"title": "Acme Corp overview", "url": "https://news.example.com/acme", "snippet": "..."},
        {"title": "Unrelated Co. news", "url": "https://news.example.com/other", "snippet": "nothing to do with it"},
    ]
    filtered = scout_utils._filter_relevant(results, "Acme Corp")
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

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

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

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

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

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

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

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

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
        side_effect=_gemini_dispatch(
            claims_json={"claims": [{"claim": "We have 50 employees", "search_query": "Acme Corp 50 employees", "source": "transcript"}]}
        )
    )

    result = await data_scout_agent.run_scout(
        "Acme Corp", "Jane Doe", None, None, "Interviewer: How many employees? Candidate: We have 50 employees, all remote."
    )

    targeted_entry = next(
        (e for e in result["sources"].get("targeted_search", []) if e["results"]), None
    )
    assert targeted_entry is not None
    assert len(targeted_entry["results"]) == 1
    assert targeted_entry["results"][0]["title"] == "Acme Corp has 50 employees"


# --- Config-driven relationship check ---------------------------------------


async def test_gather_relationship_search_disabled_when_entity_blank(patch_settings, monkeypatch):
    # Blanking scout_relationship_check_entity must skip the search entirely
    # (zero Tavily calls) - the check is a per-deployment client requirement,
    # not shared product behavior.
    patch_settings(scout_relationship_check_entity="")
    search_calls = []

    async def fake_search(query, **kwargs):
        search_calls.append(query)
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    assert await scout_gather._gather_relationship_search("Acme Corp") == []
    assert search_calls == []


async def test_gather_relationship_search_uses_configured_entity(patch_settings, monkeypatch):
    patch_settings(scout_relationship_check_entity="Globex Corporation")
    seen_queries = []

    async def fake_search(query, **kwargs):
        seen_queries.append(query)
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    await scout_gather._gather_relationship_search("Acme Corp")

    assert seen_queries == ["Acme Corp Globex Corporation"]


@respx.mock
async def test_synthesis_system_prompt_includes_relationship_check_when_entity_set(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    await data_scout_agent._synthesize_internet_findings("Acme Corp", None, {})

    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert "MANDATORY RELATIONSHIP CHECK" in system_content
    assert "Saudi Aramco" in system_content  # the shipped default entity
    assert "{entity}" not in system_content  # placeholder actually filled in


@respx.mock
async def test_synthesis_system_prompt_omits_relationship_check_when_entity_blank(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL, scout_relationship_check_entity="")
    route = respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    await data_scout_agent._synthesize_internet_findings("Acme Corp", None, {})

    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert "MANDATORY RELATIONSHIP CHECK" not in system_content
    assert "Saudi Aramco" not in system_content


# --- Pass A resilience (one source failing must not abort the others) --------


@respx.mock
async def test_run_pass_a_survives_one_source_raising(patch_settings, monkeypatch):
    # The soft-fail contract: an unexpected exception from one Pass A source
    # (here the representative search) degrades to "nothing from that source"
    # instead of cancelling its siblings and propagating out of run_scout
    # into the pipeline.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        if query == "Acme Corp overview":
            return [{"title": "Acme Corp overview", "url": "https://news.example.com/acme", "snippet": "..."}]
        return []

    async def exploding_representative_search(representative_name, company_name):
        raise ValueError("malformed search response")

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    monkeypatch.setattr(scout_gather, "_gather_representative_search", exploding_representative_search)

    state = scout_gather._GatherState()
    await scout_gather._run_pass_a(state, "Acme Corp", "Jane Doe", None, None)

    assert state.representative_search == []  # failed source degraded to empty
    assert state.blind_search[0]["query"] == "Acme Corp overview"  # sibling survived


# --- Subdomain-aware no-scrape matching --------------------------------------


async def test_derive_company_website_skips_regional_linkedin_subdomain(monkeypatch):
    # in.linkedin.com (common for Indian companies) must be excluded the same
    # as linkedin.com - exact-match-only exclusion let it through and the
    # regional profile page could be picked as the company's own website.
    async def fake_search(query, **kwargs):
        return [
            {"title": "Acme Corp - LinkedIn India", "url": "https://in.linkedin.com/company/acme", "snippet": "Acme Corp"},
            {"title": "Acme", "url": "https://www.acme.example.com/home", "snippet": "..."},
        ]

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    website = await scout_gather._derive_company_website("Acme Corp", None, None)

    assert website == "https://acme.example.com"


@respx.mock
async def test_run_scout_link_lookup_matches_no_scrape_subdomain(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_fetch_page_text(url):
        return None

    async def fake_search(query, **kwargs):
        if query.startswith("site:in.linkedin.com"):
            return [{"title": "Acme - LinkedIn", "url": "https://in.linkedin.com/company/acme", "snippet": "A company"}]
        return []

    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(side_effect=_gemini_dispatch())

    result = await data_scout_agent.run_scout(
        "Acme Corp", "Jane Doe", "Follow us at https://in.linkedin.com/company/acme", None, None
    )

    assert result["sources"]["link_lookups"][0]["domain"] == "in.linkedin.com"
    assert result["sources"]["link_lookups"][0]["full_profile_accessible"] is False


def test_matches_domain_exact_and_subdomain_but_not_lookalike():
    assert scout_utils._matches_domain("linkedin.com", ("linkedin.com",)) is True
    assert scout_utils._matches_domain("in.linkedin.com", ("linkedin.com",)) is True
    assert scout_utils._matches_domain("notlinkedin.com", ("linkedin.com",)) is False
    assert scout_utils._matches_domain("linkedin.com.evil.example", ("linkedin.com",)) is False


# --- Review-hardening regressions (feature/scout-review-hardening) -----------


def test_extract_urls_preserves_mention_order():
    # sorted() used to make "first mentioned" mean "alphabetically first" -
    # a medium.com press link would beat the company's own site.
    blobs = ["Read https://zenithcorp.example first, press at https://amedium.example/story"]
    assert scout_utils._extract_urls(blobs) == [
        "https://zenithcorp.example",
        "https://amedium.example/story",
    ]


async def test_derive_company_website_prefers_company_domain_among_mentioned_urls():
    # A third-party link mentioned earlier must not beat the company's own
    # domain - the intake branch now applies the same PREFERRED-domain
    # tiebreaker as the search fallback.
    website = await scout_gather._derive_company_website(
        "Zenith Corp",
        "Press coverage: https://amedium.example/story and our site https://zenithcorp.example/about",
        None,
    )
    assert website == "https://zenithcorp.example/about"


async def test_derive_company_website_falls_back_to_first_mentioned_without_domain_match():
    website = await scout_gather._derive_company_website(
        "Acme Corp",
        "See https://firstlink.example/a then https://secondlink.example/b",
        None,
    )
    assert website == "https://firstlink.example/a"


def test_redact_pii_preserves_headcount_prose_with_title_abbreviation():
    # Regression (executed live in review): the address regex ate
    # "150 employees led by Dr" - destroying exactly the employee/leadership
    # facts the blind searches exist to find.
    text = "150 employees led by Dr. Ahmed Al-Faisal across three offices."
    assert scout_utils._redact_pii(text) == text


def test_redact_pii_preserves_bare_financial_figures():
    # Regression: the all-separators-optional phone regex matched bare
    # 10-digit integers, redacting revenue figures.
    text = "annual revenue of 4500000000 SAR reported in 2025."
    assert scout_utils._redact_pii(text) == text


def test_redact_pii_still_masks_parenthesized_phone():
    redacted = scout_utils._redact_pii("Call (555) 123-4567 today.")
    assert "555" not in redacted
    assert "[redacted]" in redacted


def test_parse_claims_tolerates_leading_prose():
    # The hand-rolled fence-stripping choked on any prose before the JSON;
    # the shared parse_llm_json handles it.
    content = 'Here are the claims:\n{"claims": [{"claim": "Founded in 2019", "category": "specific", "search_query": "founded 2019", "source": "transcript"}]}'
    claims = scout_utils._parse_claims(content)
    assert [c["claim"] for c in claims] == ["Founded in 2019"]


def test_parse_claims_sorts_by_position_field_regardless_of_array_order():
    # The model's raw JSON array order isn't trusted - each claim's own
    # "position" (interview order, per the extraction prompt) is the sort
    # key, so a scrambled array still comes back in interview order.
    content = json.dumps({
        "claims": [
            {"claim": "Third", "category": "specific", "search_query": "c", "source": "transcript", "position": 2},
            {"claim": "First", "category": "specific", "search_query": "a", "source": "transcript", "position": 0},
            {"claim": "Second", "category": "specific", "search_query": "b", "source": "transcript", "position": 1},
        ]
    })
    claims = scout_utils._parse_claims(content)
    assert [c["claim"] for c in claims] == ["First", "Second", "Third"]
    # The returned dicts still carry exactly the pre-existing shape - no new
    # "position" key leaks into what callers already depend on.
    assert set(claims[0].keys()) == {"claim", "category", "search_query", "source"}


def test_parse_claims_falls_back_to_array_order_when_position_missing():
    # Backward-compatible: claims with no "position" field at all (or an
    # older/malformed model response) keep their original array order
    # rather than colliding or crashing.
    content = json.dumps({
        "claims": [
            {"claim": "First", "category": "specific", "search_query": "a", "source": "transcript"},
            {"claim": "Second", "category": "specific", "search_query": "b", "source": "transcript"},
        ]
    })
    claims = scout_utils._parse_claims(content)
    assert [c["claim"] for c in claims] == ["First", "Second"]


def test_parse_claims_treats_non_integer_position_as_missing():
    # A malformed "position" (e.g. the model emitting a string or bool)
    # must degrade to the array-order fallback, never raise.
    content = json.dumps({
        "claims": [
            {"claim": "First", "category": "specific", "search_query": "a", "source": "transcript", "position": "not-a-number"},
            {"claim": "Second", "category": "specific", "search_query": "b", "source": "transcript", "position": True},
        ]
    })
    claims = scout_utils._parse_claims(content)
    assert [c["claim"] for c in claims] == ["First", "Second"]


def test_scout_system_prompt_orders_claims_by_interview_position_not_verdict():
    # Confirms the fix at the prompt level: the model must no longer be
    # told to group claims by verdict label or claim type.
    prompt = settings.scout_system_prompt
    assert "same order they appear in the interview transcript" in prompt
    assert "CONTRADICTED` first" not in prompt
    assert "financial claims first" not in prompt


# --- QA fix: categorized Additional Findings (Issue 8) ----------------------


def test_scout_system_prompt_lists_all_six_finding_categories_in_order():
    prompt = settings.scout_system_prompt
    categories = ["📊 Financial", "👥 Leadership", "⚖️ Legal & Risk", "🤝 Partnerships", "👨‍💼 Workforce", "📰 Recent News"]
    positions = [prompt.index(c) for c in categories]
    assert positions == sorted(positions)


def test_scout_system_prompt_requires_omitting_empty_category_headings():
    assert 'never render an empty "### " heading' in settings.scout_system_prompt


def test_scout_system_prompt_pins_aramco_bullet_to_partnerships():
    assert "always belongs here, whether or not a relationship was found" in settings.scout_system_prompt


# --- Document Claims / Document Contradictions prompt rules (Change 1/2/3) --


def test_scout_system_prompt_declares_document_contradiction_rule():
    prompt = settings.scout_system_prompt
    assert "DOCUMENT CONTRADICTION RULE" in prompt
    assert "first check whether any uploaded document explicitly contradicts it" in prompt
    assert "do NOT use it as evidence for VERIFIED or PARTIAL" in prompt
    assert "documents are self-reported and not independent verification" in prompt
    # Must sit ahead of NO DATA's "Source count must be independent" so it
    # governs the label BEFORE the generic internet-source rules apply.
    assert prompt.index("DOCUMENT CONTRADICTION RULE") < prompt.index("Source count must be independent")


def test_scout_system_prompt_declares_document_claims_section():
    prompt = settings.scout_system_prompt
    assert "## Document Claims" in prompt
    assert "omit it entirely (no heading, no placeholder text) when there are none" in prompt
    assert "document_claims" in prompt
    assert "document_targeted_search" in prompt
    # Document Claims must sit between Interview Claims and Additional
    # Findings, per the required section order. rindex (not index): each
    # heading is also named once, earlier, in the intro paragraph that lists
    # all section names - the actual "## " heading is the LAST occurrence.
    assert prompt.rindex("## Interview Claims") < prompt.rindex("## Document Claims") < prompt.rindex("## 📌 Additional Findings")


def test_scout_system_prompt_declares_document_contradictions_summary_section():
    prompt = settings.scout_system_prompt
    assert "## Document Contradictions" in prompt
    assert "Transcript said" in prompt
    assert "Document says" in prompt
    assert "Source: Document {index}" in prompt
    assert "omit it entirely (no heading, no placeholder text) if there are none" in prompt
    # Document Contradictions is the final section, after Additional
    # Findings. rindex, not index - see the sibling test's comment above.
    assert prompt.rindex("## 📌 Additional Findings") < prompt.rindex("## Document Contradictions")


def test_scout_system_prompt_critical_data_list_includes_document_keys():
    prompt = settings.scout_system_prompt
    assert "documents, document_claims, document_targeted_search" in prompt


def test_scout_claims_report_prompt_declares_category_field_with_six_values():
    prompt = settings.scout_claims_report_prompt
    assert '"Financial" | "Leadership" | "Legal & Risk" | "Partnerships" | "Workforce" | "Recent News"' in prompt


def test_scout_claims_relationship_check_prompt_tags_partnerships_category():
    assert 'category" is always "Partnerships"' in settings.scout_claims_relationship_check_prompt


# --- QA fix: evidence directness for VERIFIED vs PARTIAL (Issue 2) ---------


def test_scout_system_prompt_requires_direct_evidence_for_verified():
    prompt = settings.scout_system_prompt
    assert "EVIDENCE DIRECTNESS" in prompt
    assert "`VERIFIED` requires at least one source that DIRECTLY confirms the claim" in prompt
    assert "use `PARTIAL` instead of `VERIFIED`" in prompt


def test_scout_claims_report_prompt_requires_direct_evidence_for_verified():
    prompt = settings.scout_claims_report_prompt
    assert "EVIDENCE DIRECTNESS" in prompt
    assert '"VERIFIED" requires at least one source that DIRECTLY confirms the claim' in prompt
    assert 'use "PARTIAL" instead of "VERIFIED"' in prompt


# --- _synthesize_claims_report: category field handling ---------------------


def _claims_report_response(claims=None, additional_findings=None) -> httpx.Response:
    body = {"claims": claims or [], "additional_findings": additional_findings or []}
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(body)}}],
        },
    )


def test_claims_report_schema_requires_category_enum_of_six_values():
    finding_schema = data_scout_agent._CLAIMS_REPORT_SCHEMA["properties"]["additional_findings"]["items"]
    assert finding_schema["required"] == ["text", "url", "category"]
    assert finding_schema["properties"]["category"]["enum"] == [
        "Financial", "Leadership", "Legal & Risk", "Partnerships", "Workforce", "Recent News",
    ]


@respx.mock
async def test_synthesize_claims_report_passes_through_valid_category(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=_claims_report_response(
            additional_findings=[{"text": "Revenue grew 20% YoY.", "url": "acme.example.com", "category": "Financial"}]
        )
    )

    result = await data_scout_agent._synthesize_claims_report("Acme Corp", None, {})

    assert result["additional_findings"] == [{"text": "Revenue grew 20% YoY.", "url": "acme.example.com", "category": "Financial"}]


@respx.mock
async def test_synthesize_claims_report_defaults_missing_category_to_recent_news(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=_claims_report_response(additional_findings=[{"text": "Some finding.", "url": "acme.example.com"}])
    )

    result = await data_scout_agent._synthesize_claims_report("Acme Corp", None, {})

    assert result["additional_findings"][0]["category"] == "Recent News"


@respx.mock
async def test_synthesize_claims_report_defaults_invalid_category_to_recent_news(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=_claims_report_response(
            additional_findings=[{"text": "Some finding.", "url": "acme.example.com", "category": "Not A Real Category"}]
        )
    )

    result = await data_scout_agent._synthesize_claims_report("Acme Corp", None, {})

    assert result["additional_findings"][0]["category"] == "Recent News"


async def test_fill_link_lookups_caps_lookup_count(patch_settings, monkeypatch):
    search_queries = []

    async def fake_search(query, **kwargs):
        search_queries.append(query)
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    urls = [f"https://linkedin.com/company/acme-{i}" for i in range(8)]
    state = scout_gather._GatherState()
    await scout_gather._fill_link_lookups(state, urls)

    assert len(search_queries) == scout_gather._MAX_LINK_LOOKUPS
    assert len(state.link_lookups) == scout_gather._MAX_LINK_LOOKUPS


@respx.mock
async def test_run_scout_fetches_duplicate_top_hit_only_once(patch_settings, monkeypatch):
    # The same URL ranking #1 for several blind queries used to pay a full
    # fetch+parse per occurrence; the per-run cache dedupes them.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    fetch_calls = []
    shared_hit = {"title": "Acme Corp profile", "url": "https://news.example.com/acme", "snippet": "Acme Corp ..."}

    async def fake_search(query, **kwargs):
        if query in ("Acme Corp overview", "Acme Corp revenue"):
            return [dict(shared_hit)]
        return []

    async def fake_fetch_page_text(url):
        fetch_calls.append(url)
        return "Acme Corp page text"

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

    assert fetch_calls.count("https://news.example.com/acme") == 1


@respx.mock
async def test_gather_timeout_preserves_already_completed_sources(patch_settings, monkeypatch):
    # THE partial-progress contract: a deadline firing while one source is
    # still running must keep everything the other sources already wrote.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    monkeypatch.setattr(data_scout_agent, "_GATHER_TIMEOUT_SECONDS", 0.3)

    async def fake_search(query, **kwargs):
        if query == "Acme Corp overview":
            return [{"title": "Acme Corp overview", "url": "https://news.example.com/acme", "snippet": "..."}]
        if query == "Jane Doe Acme Corp":
            await asyncio.sleep(5.0)  # hangs past the deadline
        return []

    async def fake_fetch_page_text(url):
        return None

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    monkeypatch.setattr("app.services.page_fetcher.fetch_page_text", fake_fetch_page_text)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

    # The hung representative search is absent, but the completed blind
    # search survived the timeout.
    assert "representative_search" not in result["sources"]
    assert result["sources"]["blind_search"][0]["query"] == "Acme Corp overview"
    assert result["findings_ok"] is True


@respx.mock
async def test_run_scout_survives_gather_raising_unexpectedly(patch_settings, monkeypatch):
    # Belt for exceptions that escape the per-source guards: synthesis still
    # runs on whatever was gathered instead of the whole report being lost.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def exploding_gather_all(state, *args, **kwargs):
        state.claims = ["We have 50 employees"]
        state.claim_details = [{"claim": "We have 50 employees", "category": "specific", "source": "transcript"}]
        raise ValueError("URL parsing edge case")

    monkeypatch.setattr(scout_gather, "_gather_all", exploding_gather_all)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "report"}}]})
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

    assert result["internet_findings"].endswith("report")
    assert result["interview_claims"] == ["We have 50 employees"]


# --- structured_findings: deterministic parse of internet_findings ---------

_SAMPLE_SYNTHESIS_MARKDOWN = """## Interview Claims

`VERIFIED` **Specializes in data engineering and AI**
Confirmed as a data engineering and AI company.
↳ ZoomInfo (zoominfo.com/c/acme) — Specializes in data engineering and analytics
↳ PR Newswire (prnewswire.com/news-releases/acme) — Global AI and data innovation company

`CONTRADICTED` **Founded in 2017**
Public records place the founding year at 2019.
↳ LinkedIn (linkedin.com/company/acme) — Company founded in 2019
↳ Crunchbase (crunchbase.com/organization/acme) — Founded 2019

## 📌 Additional Findings
- Opened a new office in Austin in 2026. ([PR Newswire](prnewswire.com/acme-austin))
- Glassdoor rating 4.1/5 from 100 reviews. ([Glassdoor](glassdoor.com/acme))
- Prior Saudi Aramco relationship found: explored an AI collaboration in 2026. ([IBM Newsroom](newsroom.ibm.com/aramco))"""


def test_split_markdown_sections_separates_headings_and_skips_verification():
    markdown = (
        "## Representative Verification\n\n**Status:** — NO DATA\n\n"
        "## Interview Claims\n\nsome claims body\n\n"
        "## 📌 Additional Findings\n\nsome findings body"
    )
    sections = data_scout_agent._split_markdown_sections(markdown)
    assert data_scout_agent._find_section(sections, "Interview Claims") == "some claims body"
    assert data_scout_agent._find_section(sections, "Additional Findings") == "some findings body"
    assert data_scout_agent._find_section(sections, "Representative Verification").startswith("**Status:**")


def test_parse_claims_section_empty_placeholder_returns_empty_list():
    assert data_scout_agent._parse_claims_section("No claims required attention.") == []


def test_parse_claims_section_parses_label_claim_description_and_sources():
    body = data_scout_agent._find_section(
        data_scout_agent._split_markdown_sections(_SAMPLE_SYNTHESIS_MARKDOWN), "Interview Claims"
    )
    claims = data_scout_agent._parse_claims_section(body)
    assert claims == [
        {
            "claim": "Specializes in data engineering and AI",
            "label": "VERIFIED",
            "description": "Confirmed as a data engineering and AI company.",
            "sources": [
                {"name": "ZoomInfo", "url": "zoominfo.com/c/acme", "note": "Specializes in data engineering and analytics"},
                {"name": "PR Newswire", "url": "prnewswire.com/news-releases/acme", "note": "Global AI and data innovation company"},
            ],
        },
        {
            "claim": "Founded in 2017",
            "label": "CONTRADICTED",
            "description": "Public records place the founding year at 2019.",
            "sources": [
                {"name": "LinkedIn", "url": "linkedin.com/company/acme", "note": "Company founded in 2019"},
                {"name": "Crunchbase", "url": "crunchbase.com/organization/acme", "note": "Founded 2019"},
            ],
        },
    ]


def test_parse_claims_section_skips_malformed_block_without_dropping_others():
    body = "not a valid claim header at all\n\n`VERIFIED` **Real claim**\nDescription text.\n↳ Source (url) — note"
    claims = data_scout_agent._parse_claims_section(body)
    assert len(claims) == 1
    assert claims[0]["claim"] == "Real claim"


def test_parse_additional_findings_section_empty_placeholder_returns_empty_list():
    assert data_scout_agent._parse_additional_findings_section("No additional findings.") == []


def test_parse_additional_findings_section_parses_bullets_with_and_without_source():
    body = data_scout_agent._find_section(
        data_scout_agent._split_markdown_sections(_SAMPLE_SYNTHESIS_MARKDOWN), "Additional Findings"
    )
    findings = data_scout_agent._parse_additional_findings_section(body)
    assert findings == [
        {"text": "Opened a new office in Austin in 2026.", "source": {"name": "PR Newswire", "url": "prnewswire.com/acme-austin"}},
        {"text": "Glassdoor rating 4.1/5 from 100 reviews.", "source": {"name": "Glassdoor", "url": "glassdoor.com/acme"}},
        {
            "text": "Prior Saudi Aramco relationship found: explored an AI collaboration in 2026.",
            "source": {"name": "IBM Newsroom", "url": "newsroom.ibm.com/aramco"},
        },
    ]


def test_parse_additional_findings_no_source_sentence_kept_with_null_source():
    findings = data_scout_agent._parse_additional_findings_section(
        "- No prior Saudi Aramco relationship found in public records."
    )
    assert findings == [{"text": "No prior Saudi Aramco relationship found in public records.", "source": None}]


def test_extract_relationship_finding_returns_detail_when_found():
    findings = [{"text": "Prior Saudi Aramco relationship found: explored AI collaboration in 2026.", "source": None}]
    assert (
        data_scout_agent._extract_relationship_finding(findings, "Saudi Aramco")
        == "explored AI collaboration in 2026."
    )


def test_extract_relationship_finding_returns_none_when_explicitly_not_found():
    findings = [{"text": "No prior Saudi Aramco relationship found in public records.", "source": None}]
    assert data_scout_agent._extract_relationship_finding(findings, "Saudi Aramco") is None


def test_extract_relationship_finding_returns_none_when_bullet_absent():
    assert data_scout_agent._extract_relationship_finding([{"text": "Unrelated bullet.", "source": None}], "Saudi Aramco") is None


def test_extract_relationship_finding_returns_none_when_entity_blank():
    assert data_scout_agent._extract_relationship_finding([{"text": "anything", "source": None}], "") is None


def test_guess_claim_category_matches_best_overlap_and_defaults_to_general():
    claim_details = [
        {"claim": "We have 50 employees", "category": "specific"},
        {"claim": "Global presence across many countries", "category": "general"},
    ]
    assert data_scout_agent._guess_claim_category("We have 50 employees", claim_details) == "specific"
    assert data_scout_agent._guess_claim_category("Something totally unrelated", claim_details) == "general"


def test_build_structured_findings_none_when_findings_not_ok():
    assert data_scout_agent._build_structured_findings("some markdown", False, "https://acme.example.com", []) is None


def test_build_structured_findings_none_when_markdown_blank():
    assert data_scout_agent._build_structured_findings("   ", True, None, []) is None


def test_build_structured_findings_none_when_required_sections_missing():
    assert data_scout_agent._build_structured_findings("## Some Other Heading\n\nbody", True, None, []) is None


def test_build_structured_findings_degrades_to_none_on_unexpected_exception(monkeypatch):
    def exploding_split(markdown):
        raise ValueError("boom")

    monkeypatch.setattr(data_scout_agent, "_split_markdown_sections", exploding_split)
    assert data_scout_agent._build_structured_findings(_SAMPLE_SYNTHESIS_MARKDOWN, True, None, []) is None


@respx.mock
async def test_run_scout_populates_structured_findings_on_synthesis_success(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL, scout_relationship_check_entity="Saudi Aramco")

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=_gemini_dispatch(synthesis_content=_SAMPLE_SYNTHESIS_MARKDOWN)
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

    assert result["findings_ok"] is True
    structured = result["structured_findings"]
    assert structured is not None
    assert structured["company_website"] == result["company_website"]
    assert structured["findings_ok"] is True
    assert structured["aramco_relationship"] == "explored an AI collaboration in 2026."
    assert len(structured["claims"]) == 2
    assert structured["claims"][0]["claim"] == "Specializes in data engineering and AI"
    assert structured["claims"][0]["label"] == "VERIFIED"
    assert structured["claims"][0]["category"] in ("specific", "general")
    assert len(structured["additional_findings"]) == 3

    # Regression: every pre-existing ScoutReport field is still present and
    # of its expected type - adding structured_findings must not have
    # displaced or renamed anything.
    assert set(result.keys()) == {
        "internet_findings",
        "interview_claims",
        "sources",
        "findings_ok",
        "company_website",
        "representative_verification",
        "claims_report",
        "structured_findings",
    }
    assert isinstance(result["internet_findings"], str)
    assert isinstance(result["interview_claims"], list)
    assert isinstance(result["sources"], dict)
    assert isinstance(result["findings_ok"], bool)


@respx.mock
async def test_run_scout_structured_findings_claims_match_internet_findings_markdown(patch_settings, monkeypatch):
    # The structured claims must reflect exactly what's rendered in the
    # existing internet_findings markdown - same labels, same claim text,
    # same source count - since structured_findings is a parse of that exact
    # text, not an independent judgment.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=_gemini_dispatch(synthesis_content=_SAMPLE_SYNTHESIS_MARKDOWN)
    )

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

    markdown = result["internet_findings"]
    structured = result["structured_findings"]
    for claim in structured["claims"]:
        assert f"`{claim['label']}` **{claim['claim']}**" in markdown
        for source in claim["sources"]:
            assert f"↳ {source['name']} ({source['url']}) — {source['note']}" in markdown


@respx.mock
async def test_run_scout_structured_findings_none_when_synthesis_fails(patch_settings, monkeypatch):
    # findings_ok False (synthesis failed) must degrade structured_findings
    # to None too - never a partially-populated/misleading structure.
    patch_settings(gemini_api_key=None)

    async def fake_search(query, **kwargs):
        return []

    monkeypatch.setattr("app.services.web_search_client.search", fake_search)

    result = await data_scout_agent.run_scout("Acme Corp", "Jane Doe", None, None, None)

    assert result["findings_ok"] is False
    assert result["structured_findings"] is None
    # internet_findings must still be returned correctly (the independently
    # computed Representative Verification section) regardless of the
    # structured_findings degradation.
    assert result["internet_findings"].startswith("## Representative Verification")
