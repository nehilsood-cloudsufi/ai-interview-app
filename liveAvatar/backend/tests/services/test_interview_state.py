from datetime import datetime, timedelta, timezone

import yaml

from app.services import interview_state
from app.services.interview_state import VendorProfile

DOMAIN = "ai_ml"


def make_profile(**overrides):
    defaults = dict(
        company_name="Acme Corp",
        contact_name="Jane Doe",
        contact_role="CTO",
    )
    defaults.update(overrides)
    return VendorProfile(**defaults)


def test_create_returns_state_with_defaults():
    state = interview_state.create(make_profile(), DOMAIN)
    assert state.interview_id
    assert state.gateway_token
    assert state.domain == DOMAIN
    assert state.status == "created"
    assert state.current_node_id == "company_overview"  # first node of the shipped questionnaire
    assert state.followup_count == 0
    assert state.turns == []
    assert state.scout_findings == []


def test_vendor_profile_has_empty_defaults():
    profile = VendorProfile()
    assert profile.company_name == ""
    assert profile.contact_name == ""
    assert profile.contact_role is None


def test_create_works_with_no_args_profile():
    # Profile is filled in later by conversation, not at creation time.
    state = interview_state.create(VendorProfile(), DOMAIN)
    assert state.interview_id
    assert state.vendor_profile.company_name == ""


def test_create_generates_unique_ids_and_tokens():
    state1 = interview_state.create(make_profile(), DOMAIN)
    state2 = interview_state.create(make_profile(), DOMAIN)
    assert state1.interview_id != state2.interview_id
    assert state1.gateway_token != state2.gateway_token


def test_create_stores_domain_and_resolves_domain_start_node(tmp_path, patch_settings):
    # A domain other than the default must still be stored on the state and
    # resolve to THAT domain's own start node, not some other domain's.
    directory = tmp_path / "questionnaires"
    directory.mkdir()
    patch_settings(questionnaires_dir=str(directory))
    (directory / "widgets.yaml").write_text(
        yaml.safe_dump(
            {
                "domain": "widgets",
                "title": "Widgets",
                "questions": [
                    {
                        "id": "widget_intro",
                        "topic": "onboarding",
                        "ask": "Greet the vendor.",
                        "rubric_categories": [],
                        "next": "END",
                    }
                ],
            }
        )
    )

    state = interview_state.create(make_profile(), "widgets")

    assert state.domain == "widgets"
    assert state.current_node_id == "widget_intro"


def test_get_roundtrip():
    state = interview_state.create(make_profile(), DOMAIN)
    assert interview_state.get(state.interview_id) is state


def test_get_missing_returns_none():
    assert interview_state.get("does-not-exist") is None


def test_prune_older_than_removes_stale_keeps_fresh():
    fresh = interview_state.create(make_profile(), DOMAIN)
    stale = interview_state.create(make_profile(company_name="Stale Co"), DOMAIN)
    stale.created_at = datetime.now(timezone.utc) - timedelta(hours=7)

    removed = interview_state.prune_older_than(hours=6)

    assert removed == 1
    assert interview_state.get(fresh.interview_id) is not None
    assert interview_state.get(stale.interview_id) is None
