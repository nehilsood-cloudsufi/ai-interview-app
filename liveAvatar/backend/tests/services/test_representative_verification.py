import json

import httpx
import respx

from app.services import representative_verification, scout_utils
from app.services.representative_verification import verify_representative

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
CHAT_URL = f"{GEMINI_BASE_URL}chat/completions"


def _search_entry(query: str, results: list[dict]) -> list[dict]:
    return [{"query": query, "results": results}]


def _hit(title: str, url: str, snippet: str) -> dict:
    return {"title": title, "url": url, "snippet": snippet, "text": None}


def _extraction_response(**overrides) -> httpx.Response:
    body = {
        "tied_to_stated_company": False,
        "role": "unknown",
        "background": {"tenure": "not found", "projects": [], "focus_areas": []},
        "summary": "No public data found.",
        "citations": [],
    }
    body.update(overrides)
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(body)}}]})


# --- _check_email -----------------------------------------------------------


def test_check_email_missing():
    assert scout_utils._check_email("") == {"email_valid": False, "email_type": "missing", "email_domain": ""}
    assert scout_utils._check_email("   ") == {"email_valid": False, "email_type": "missing", "email_domain": ""}


def test_check_email_invalid_format():
    result = scout_utils._check_email("not-an-email")
    assert result == {"email_valid": False, "email_type": "invalid", "email_domain": ""}


def test_check_email_corporate():
    result = scout_utils._check_email("karan.sachdeva@ibm.com")
    assert result == {"email_valid": True, "email_type": "corporate", "email_domain": "ibm.com"}


def test_check_email_personal(patch_settings):
    result = scout_utils._check_email("jane.doe@gmail.com")
    assert result == {"email_valid": True, "email_type": "personal", "email_domain": "gmail.com"}


def test_check_email_personal_matches_subdomain_style_suffix():
    # _matches_domain is suffix-aware - a personal-domain classification must
    # follow that same rule as every other domain check in this module.
    result = scout_utils._check_email("someone@mail.proton.me")
    assert result["email_type"] == "personal"


# --- _company_names_match ----------------------------------------------------


def test_company_names_match_substring_either_direction():
    assert scout_utils._company_names_match("IBM", "IBM Corporation") is True
    assert scout_utils._company_names_match("IBM Corporation", "IBM") is True


def test_company_names_match_case_insensitive():
    assert scout_utils._company_names_match("ibm", "IBM") is True


def test_company_names_match_shared_token_suffix_variance():
    assert scout_utils._company_names_match("Acme", "Acme Corp, Inc.") is True


def test_company_names_match_false_for_different_companies():
    assert scout_utils._company_names_match("IBM", "Microsoft") is False


def test_company_names_match_false_for_blank_inputs():
    assert scout_utils._company_names_match("", "IBM") is False
    assert scout_utils._company_names_match("IBM", "") is False


# --- _is_unknown --------------------------------------------------------------


def test_is_unknown_recognizes_placeholder_synonyms():
    assert scout_utils._is_unknown("none") is True
    assert scout_utils._is_unknown("None") is True
    assert scout_utils._is_unknown("unknown") is True
    assert scout_utils._is_unknown("not found") is True
    assert scout_utils._is_unknown("") is True
    assert scout_utils._is_unknown(None) is True
    assert scout_utils._is_unknown("  none  ") is True


def test_is_unknown_false_for_a_real_value():
    assert scout_utils._is_unknown("IBM") is False


# --- verify_representative: NO_DATA (technical failure - could not run) -----


async def test_no_data_when_representative_name_blank(patch_settings):
    patch_settings(gemini_api_key="gem-key")
    result = await verify_representative("", "IBM", "karan@ibm.com", [])
    assert result.verdict == "NO_DATA"


async def test_no_data_when_no_gemini_key_configured(patch_settings):
    patch_settings(gemini_api_key=None)
    person_entries = _search_entry(
        "Karan Sachdeva IBM", [_hit("Karan Sachdeva - LinkedIn", "https://linkedin.com/in/karansachdeva", "Works at IBM")]
    )
    result = await verify_representative("Karan Sachdeva", "IBM", "karan@ibm.com", person_entries)
    assert result.verdict == "NO_DATA"


# --- THE CRITICAL BUG REGRESSION TEST ----------------------------------------


@respx.mock
async def test_matching_email_domain_alone_does_not_verify(patch_settings, monkeypatch):
    # THE bug this module was rewritten to fix: a fabricated/random person
    # with a hand-typed email whose domain happens to match the stated
    # company must NEVER be VERIFIED on that basis alone.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=_extraction_response())
    person_entries = _search_entry(
        "Pooja Tripathi IBM",
        [
            _hit("Pooja Tripathi - Product School", "https://productschool.com/product-leaders/pooja-tripathi", "Product leader and speaker."),
            _hit("Pooja Tripathi - Influential Women", "https://influentialwomen.com/pooja-tripathi", "Recognized as an influential woman in tech."),
        ],
    )

    result = await verify_representative("Pooja Tripathi", "IBM", "pooja.tripathi@ibm.com", person_entries)

    assert result.verdict == "NOT_FOUND"
    assert result.email_domain_plausible is True  # the domain DOES match IBM's name...
    assert result.evidence == []

    section = representative_verification.render_representative_verification(
        "Pooja Tripathi", "IBM", "pooja.tripathi@ibm.com", result
    )
    assert "VERIFIED" not in section
    assert "**Status:** ✗ NOT FOUND" in section
    # ...but the rendered note must make explicit that the domain match does
    # NOT confirm employment - this is the exact honesty requirement.
    assert "Uses IBM's domain, but no public evidence links this person to IBM. Domain match is not proof." in section


# --- verify_representative: VERIFIED (evidence-driven only) ------------------


@respx.mock
async def test_verified_with_evidence_citations(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=_extraction_response(
            tied_to_stated_company=True,
            role="SVP, Software & Chief Commercial Officer",
            background={"tenure": "20+ years at IBM", "projects": ["The AI Ladder"], "focus_areas": ["AI", "Hybrid Cloud"]},
            summary="20+ years at IBM. Confirmed across independent public sources with consistent role and history.",
            citations=[
                {"source_index": 1, "supports": "confirms current SVP role at IBM"},
                {"source_index": 2, "supports": "states 20 years at IBM in data & AI"},
            ],
        )
    )
    person_entries = _search_entry(
        "Rob Thomas IBM",
        [
            _hit("Rob Thomas - LinkedIn", "https://linkedin.com/in/robertdthomas", "SVP, Software at IBM"),
            _hit("Rob Thomas - About", "https://robdthomas.com/about", "20 years at IBM in data & AI"),
        ],
    )

    result = await verify_representative("Rob Thomas", "IBM", "", person_entries)

    assert result.verdict == "VERIFIED"
    assert not hasattr(result, "confidence")
    assert result.evidence == [
        {"url": "https://linkedin.com/in/robertdthomas", "source_name": "LinkedIn", "supports": "confirms current SVP role at IBM"},
        {"url": "https://robdthomas.com/about", "source_name": "robdthomas.com", "supports": "states 20 years at IBM in data & AI"},
    ]

    section = representative_verification.render_representative_verification("Rob Thomas", "IBM", "", result)
    assert "**Status:** ✓ VERIFIED" in section
    assert "confidence" not in section.lower()
    assert "**Role:** SVP, Software & Chief Commercial Officer" in section
    assert "**Company:** IBM" in section
    assert "**Focus areas:** AI · Hybrid Cloud" in section
    assert "**Known work:** The AI Ladder" in section
    assert "**Evidence:**" in section
    assert "- **LinkedIn** — confirms current SVP role at IBM" in section
    assert "  ↳ linkedin.com/in/robertdthomas" in section
    assert "- **robdthomas.com** — states 20 years at IBM in data & AI" in section
    # NO reviewer-action line anywhere, and no same-name caution.
    assert "Reviewer action" not in section
    assert "manual verification recommended" not in section
    assert "multiple professionals may share this name" not in section


# --- verify_representative: NOT_FOUND (name matches, no company tie) --------


@respx.mock
async def test_not_found_when_no_company_scoped_evidence_at_all(patch_settings):
    # No person_search_entries at all - short-circuits to NOT_FOUND without
    # even calling the LLM.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=_extraction_response())

    result = await verify_representative("Jane Doe", "Acme Corp", "", [])

    assert result.verdict == "NOT_FOUND"
    assert route.call_count == 0
    section = representative_verification.render_representative_verification("Jane Doe", "Acme Corp", "", result)
    assert "**Evidence:**" not in section
    assert "**Company:** Acme Corp (claimed)" in section
    assert "Reviewer action" not in section


@respx.mock
async def test_not_found_names_no_other_employer(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=_extraction_response(tied_to_stated_company=False))
    person_entries = _search_entry(
        "Jane Doe Acme Corp",
        [_hit("Acme Corp news", "https://news.example.com/acme", "Acme Corp and Jane Doe both mentioned in an unrelated roundup article.")],
    )

    result = await verify_representative("Jane Doe", "Acme Corp", "", person_entries)

    assert result.verdict == "NOT_FOUND"
    section = representative_verification.render_representative_verification("Jane Doe", "Acme Corp", "", result)
    assert "No public records associate this person with Acme Corp." in section
    assert "**Role:**" not in section


# --- NO_DATA ------------------------------------------------------------------


def test_render_no_data_blank_name():
    verification = representative_verification._no_data({"email_valid": False, "email_type": "missing", "email_domain": ""}, "Acme Corp")
    section = representative_verification.render_representative_verification("", "Acme Corp", "", verification)
    assert section == (
        "## Representative Verification\n\n"
        "**Status:** — NO DATA\n"
        "**Name:** (blank / not provided)\n\n"
        "**Summary:** Verification could not run (no representative name provided, or the identity "
        "search was unavailable)."
    )
    assert "Reviewer action" not in section


def test_render_no_data_with_name_shows_company_claimed():
    verification = representative_verification._no_data(
        {"email_valid": False, "email_type": "missing", "email_domain": ""}, "Acme Corp",
        "Verification could not be completed due to an internal error.",
    )
    section = representative_verification.render_representative_verification("Jane Doe", "Acme Corp", "", verification)
    assert "**Status:** — NO DATA" in section
    assert "**Name:** Jane Doe" in section
    assert "**Company:** Acme Corp (claimed)" in section
    assert "Reviewer action" not in section


# --- email: plausible, never verified ----------------------------------------


def test_email_note_verified_corporate_matching_domain():
    verification = representative_verification.RepresentativeVerification(
        verdict="VERIFIED", email_valid=True, email_type="corporate", email_domain="ibm.com",
        email_domain_plausible=True, role="Executive", summary="Confirmed.",
        background={"tenure": "1 year", "projects": [], "focus_areas": []}, evidence=[],
    )
    section = representative_verification.render_representative_verification(
        "Rob Thomas", "IBM", "rob.thomas@ibm.com", verification
    )
    assert "**Email:** rob.thomas@ibm.com" in section
    assert "Uses IBM's corporate domain (consistent with the verified identity)." in section


def test_email_note_verified_personal():
    verification = representative_verification.RepresentativeVerification(
        verdict="VERIFIED", email_valid=True, email_type="personal", email_domain="gmail.com",
        email_domain_plausible=False, role="Executive", summary="Confirmed.",
        background={"tenure": "1 year", "projects": [], "focus_areas": []}, evidence=[],
    )
    section = representative_verification.render_representative_verification(
        "Rob Thomas", "IBM", "rob.thomas@gmail.com", verification
    )
    assert "**Email:** rob.thomas@gmail.com" in section
    assert "Personal address — not used for verification. Identity confirmed via public sources instead." in section


def test_email_note_absent_when_no_email_provided():
    verification = representative_verification._not_found({"email_valid": False, "email_type": "missing", "email_domain": ""}, "Acme Corp")
    section = representative_verification.render_representative_verification("Jane Doe", "Acme Corp", "", verification)
    assert "Email:" not in section


# --- soft-fail: LLM raises -> NO_DATA, pipeline still completes -------------


@respx.mock
async def test_llm_call_raises_degrades_to_no_data_not_propagated(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500))
    person_entries = _search_entry(
        "Jane Doe Acme Corp", [_hit("Jane Doe at Acme Corp", "https://linkedin.com/in/janedoe", "Works at Acme Corp")]
    )

    result = await verify_representative("Jane Doe", "Acme Corp", "jane.doe@acme.com", person_entries)

    assert result.verdict == "NO_DATA"


@respx.mock
async def test_malformed_json_degrades_to_no_data(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "not json at all"}}]})
    )
    person_entries = _search_entry(
        "Jane Doe Acme Corp", [_hit("Jane Doe at Acme Corp", "https://linkedin.com/in/janedoe", "Works at Acme Corp")]
    )

    result = await verify_representative("Jane Doe", "Acme Corp", "jane.doe@acme.com", person_entries)

    assert result.verdict == "NO_DATA"


# --- anti-hallucination -------------------------------------------------------


@respx.mock
async def test_no_named_projects_in_evidence_stays_empty_not_invented(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=_extraction_response(
            tied_to_stated_company=True,
            role="Engineer",
            background={"tenure": "2 years", "projects": [], "focus_areas": []},
            citations=[{"source_index": 1, "supports": "confirms Engineer role at Acme Corp"}],
        )
    )
    person_entries = _search_entry(
        "Jane Doe Acme Corp", [_hit("Jane Doe - LinkedIn", "https://linkedin.com/in/janedoe", "Works at Acme Corp")]
    )

    result = await verify_representative("Jane Doe", "Acme Corp", "", person_entries)

    assert result.verdict == "VERIFIED"
    assert result.background["projects"] == []
    assert result.background["focus_areas"] == []
    section = representative_verification.render_representative_verification("Jane Doe", "Acme Corp", "", result)
    assert "**Known work:**" not in section
    assert "**Focus areas:**" not in section


@respx.mock
async def test_citation_supports_phrase_only_reflects_the_cited_evidence(patch_settings, monkeypatch):
    # Anti-hallucination for the "supports" field: an out-of-range or
    # malformed citation index must be dropped, never invented/crash.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=_extraction_response(
            tied_to_stated_company=True,
            role="Engineer",
            background={"tenure": "2 years", "projects": [], "focus_areas": []},
            citations=[
                {"source_index": 1, "supports": "confirms Engineer role at Acme Corp"},
                {"source_index": 99, "supports": "a hallucinated out-of-range citation"},
            ],
        )
    )
    person_entries = _search_entry(
        "Jane Doe Acme Corp", [_hit("Jane Doe - LinkedIn", "https://linkedin.com/in/janedoe", "Works at Acme Corp")]
    )

    result = await verify_representative("Jane Doe", "Acme Corp", "", person_entries)

    assert len(result.evidence) == 1
    assert result.evidence[0]["supports"] == "confirms Engineer role at Acme Corp"


# --- the VERIFIED-requires-evidence guard ------------------------------------


@respx.mock
async def test_guard_downgrades_tied_true_with_no_facts_to_not_found(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=_extraction_response(tied_to_stated_company=True, role="unknown")
    )
    person_entries = _search_entry(
        "Jane Doe Acme Corp", [_hit("Jane Doe - Acme Corp", "https://linkedin.com/in/janedoe", "Mentions Acme Corp")]
    )

    result = await verify_representative("Jane Doe", "Acme Corp", "", person_entries)

    assert result.verdict == "NOT_FOUND"
    assert result.evidence == []


def test_guard_is_a_noop_for_a_legitimately_verified_result():
    verification = representative_verification.RepresentativeVerification(
        verdict="VERIFIED", email_valid=False, email_type="missing", email_domain="",
        email_domain_plausible=False, role="VP Sales", summary="",
        background={"tenure": "3 years", "projects": [], "focus_areas": []}, evidence=[],
    )
    guarded = representative_verification._guard_verified_requires_evidence(verification)
    assert guarded.verdict == "VERIFIED"


# --- confidence / reviewer-action removal ------------------------------------


def test_dataclass_has_no_confidence_field():
    fields = {f.name for f in representative_verification.dataclasses.fields(representative_verification.RepresentativeVerification)}
    assert "confidence" not in fields


def test_no_reviewer_action_function_exists():
    assert not hasattr(representative_verification, "_reviewer_action")
