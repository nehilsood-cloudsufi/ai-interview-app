from datetime import datetime, timedelta, timezone

from app.services import interview_state
from app.services.interview_state import VendorProfile


def make_profile(**overrides):
    defaults = dict(
        company_name="Acme Corp",
        website="https://acme.example",
        contact_name="Jane Doe",
        contact_role="CTO",
    )
    defaults.update(overrides)
    return VendorProfile(**defaults)


def test_create_returns_state_with_defaults():
    state = interview_state.create(make_profile())
    assert state.interview_id
    assert state.gateway_token
    assert state.status == "created"
    assert state.current_node_id == "company_overview"  # first node of the shipped questionnaire
    assert state.followup_count == 0
    assert state.turns == []
    assert state.scout_findings == []


def test_create_generates_unique_ids_and_tokens():
    state1 = interview_state.create(make_profile())
    state2 = interview_state.create(make_profile())
    assert state1.interview_id != state2.interview_id
    assert state1.gateway_token != state2.gateway_token


def test_get_roundtrip():
    state = interview_state.create(make_profile())
    assert interview_state.get(state.interview_id) is state


def test_get_missing_returns_none():
    assert interview_state.get("does-not-exist") is None


def test_get_by_token_roundtrip():
    state = interview_state.create(make_profile())
    assert interview_state.get_by_token(state.gateway_token) is state


def test_get_by_token_missing_returns_none():
    assert interview_state.get_by_token("bogus-token") is None


def test_get_by_token_finds_correct_state_among_several():
    first = interview_state.create(make_profile(company_name="First Co"))
    second = interview_state.create(make_profile(company_name="Second Co"))
    assert interview_state.get_by_token(second.gateway_token) is second
    assert interview_state.get_by_token(first.gateway_token) is first


def test_remove():
    state = interview_state.create(make_profile())
    interview_state.remove(state.interview_id)
    assert interview_state.get(state.interview_id) is None


def test_remove_missing_is_a_noop():
    interview_state.remove("does-not-exist")


def test_prune_older_than_removes_stale_keeps_fresh():
    fresh = interview_state.create(make_profile())
    stale = interview_state.create(make_profile(company_name="Stale Co"))
    stale.created_at = datetime.now(timezone.utc) - timedelta(hours=7)

    removed = interview_state.prune_older_than(hours=6)

    assert removed == 1
    assert interview_state.get(fresh.interview_id) is not None
    assert interview_state.get(stale.interview_id) is None
