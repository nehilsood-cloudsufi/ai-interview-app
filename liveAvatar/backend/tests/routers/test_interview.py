import dataclasses
import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.config import settings
from app.models import TranscriptTurn
from app.services import host_agent, interview_state
from app.services.coordinator_agent import FollowupRecommendation
from app.services.evaluator_agent import CategoryScore, Scorecard
from app.services.host_agent import TurnResult
from app.services.interview_state import ScoutFinding, VendorProfile

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
CHAT_URL = f"{GEMINI_BASE_URL}chat/completions"


def _seed_interview(domain="ai_ml"):
    return interview_state.create(
        VendorProfile(
            company_name="Acme Corp",
            contact_name="Jane Doe",
            contact_role="CTO",
        ),
        domain,
    )


def _url(interview_id: str) -> str:
    return f"/api/interview/{interview_id}/state"


def test_create_interview_returns_empty_profile_interview(client):
    response = client.post("/api/interview")

    assert response.status_code == 200
    body = response.json()
    interview_id = body["interview_id"]
    assert interview_id

    state = interview_state.get(interview_id)
    assert state is not None
    assert state.vendor_profile == VendorProfile()


def test_create_interview_with_no_body_defaults_domain(client):
    # The frontend currently POSTs with no body at all - must not 422.
    response = client.post("/api/interview")

    assert response.status_code == 200
    state = interview_state.get(response.json()["interview_id"])
    assert state.domain == "frontier_tech"


def test_create_interview_with_null_domain_defaults_domain(client):
    response = client.post("/api/interview", json={"domain": None})

    assert response.status_code == 200
    state = interview_state.get(response.json()["interview_id"])
    assert state.domain == "frontier_tech"


def test_create_interview_with_valid_domain(client):
    response = client.post("/api/interview", json={"domain": "cloud_infrastructure"})

    assert response.status_code == 200
    state = interview_state.get(response.json()["interview_id"])
    assert state.domain == "cloud_infrastructure"


def test_create_interview_defaults_to_dev_tier(client):
    response = client.post("/api/interview")

    assert response.status_code == 200
    state = interview_state.get(response.json()["interview_id"])
    assert state.tier == "dev"


def test_create_interview_with_unknown_tier_returns_400(client):
    response = client.post("/api/interview", json={"tier": "staging"})

    assert response.status_code == 400
    assert "staging" in response.json()["detail"]


def test_create_interview_prod_tier_unconfigured_returns_503(client, patch_settings):
    patch_settings(prod_avatar_id=None, demo_passcode=None)

    response = client.post("/api/interview", json={"tier": "prod", "passcode": "anything"})

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_create_interview_prod_tier_wrong_passcode_returns_403(client, patch_settings):
    patch_settings(prod_avatar_id="avatar-june", demo_passcode="s3cret")

    response = client.post("/api/interview", json={"tier": "prod", "passcode": "wrong"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid passcode"


def test_create_interview_prod_tier_missing_passcode_returns_403(client, patch_settings):
    patch_settings(prod_avatar_id="avatar-june", demo_passcode="s3cret")

    response = client.post("/api/interview", json={"tier": "prod"})

    assert response.status_code == 403


def test_create_interview_prod_tier_happy_path(client, patch_settings):
    patch_settings(prod_avatar_id="avatar-june", demo_passcode="s3cret")

    response = client.post("/api/interview", json={"tier": "prod", "passcode": "s3cret"})

    assert response.status_code == 200
    state = interview_state.get(response.json()["interview_id"])
    assert state.tier == "prod"


def test_create_interview_prod_tier_default_duration_5_min(client, patch_settings):
    patch_settings(prod_avatar_id="avatar-june", demo_passcode="s3cret")

    response = client.post("/api/interview", json={"tier": "prod", "passcode": "s3cret"})

    assert response.status_code == 200
    state = interview_state.get(response.json()["interview_id"])
    assert state.max_session_seconds == 300


def test_create_interview_prod_tier_custom_duration(client, patch_settings):
    patch_settings(prod_avatar_id="avatar-june", demo_passcode="s3cret")

    response = client.post(
        "/api/interview", json={"tier": "prod", "passcode": "s3cret", "duration_minutes": 10}
    )

    assert response.status_code == 200
    state = interview_state.get(response.json()["interview_id"])
    assert state.max_session_seconds == 600


@pytest.mark.parametrize("minutes", [0, -1, 11])
def test_create_interview_prod_tier_duration_out_of_range_400(client, patch_settings, minutes):
    # The ceiling is PROD_MAX_SESSION_SECONDS (default 600 = 10 min).
    patch_settings(prod_avatar_id="avatar-june", demo_passcode="s3cret")

    response = client.post(
        "/api/interview", json={"tier": "prod", "passcode": "s3cret", "duration_minutes": minutes}
    )

    assert response.status_code == 400
    assert "duration_minutes" in response.json()["detail"]


def test_create_interview_dev_tier_ignores_duration(client):
    response = client.post("/api/interview", json={"tier": "dev", "duration_minutes": 99})

    assert response.status_code == 200
    state = interview_state.get(response.json()["interview_id"])
    assert state.max_session_seconds is None


def test_create_interview_dev_tier_ignores_passcode(client, patch_settings):
    # Dev tier never checks the passcode, even when prod is configured.
    patch_settings(prod_avatar_id="avatar-june", demo_passcode="s3cret")

    response = client.post("/api/interview", json={"tier": "dev", "passcode": "wrong"})

    assert response.status_code == 200


def test_create_interview_with_unknown_domain_returns_400(client):
    response = client.post("/api/interview", json={"domain": "not_a_real_domain"})

    assert response.status_code == 400
    assert interview_state._interviews == {}


def test_get_domains(client):
    response = client.get("/api/domains")

    assert response.status_code == 200
    body = response.json()
    ids = [d["id"] for d in body["domains"]]
    assert ids == sorted(ids)
    assert set(ids) == {"ai_ml", "cloud_infrastructure", "data_engineering", "frontier_tech"}
    for entry in body["domains"]:
        assert entry["title"]
    # The picker preselects the server's default rather than the first entry.
    assert body["default"] == settings.default_domain
    assert body["default"] in ids


def test_state_reports_done_only_at_end(client):
    created = client.post("/api/interview", json={"domain": "ai_ml"}).json()
    interview_id = created["interview_id"]

    body = client.get(f"/api/interview/{interview_id}/state").json()
    assert body["done"] is False

    # Reaching END flips done - the avatar frontend auto-stops on this.
    interview_state.get(interview_id).current_node_id = host_agent.END_NODE_ID
    body = client.get(f"/api/interview/{interview_id}/state").json()
    assert body["done"] is True
    assert body["current_topic"] is None


def test_create_interview_id_resolves_via_get_state(client):
    response = client.post("/api/interview")
    interview_id = response.json()["interview_id"]

    state_response = client.get(_url(interview_id))

    assert state_response.status_code == 200
    body = state_response.json()
    assert body["status"] == "created"
    # The start node is the default (frontier_tech) questionnaire's first
    # substantive question - onboarding nodes are gone, the profile comes
    # from the start screen's intake form.
    assert body["current_topic"] == "theme_interest"


def test_unknown_interview_404(client):
    response = client.get(_url("nope"))
    assert response.status_code == 404


def test_fresh_interview_state(client):
    state = _seed_interview()

    response = client.get(_url(state.interview_id))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "created"
    assert body["domain"] == "ai_ml"
    # The start node is the questionnaire's first substantive question.
    assert body["current_topic"] == "company_overview"

    # Scoring is a single holistic pass at finalize, run by the background
    # pipeline - the live-state snapshot carries the pipeline's progress
    # (None until finalize hands the interview off) rather than running its
    # own scoring.
    assert body["pipeline_status"] is None
    assert body["scorecard"] is None
    assert body["recommendation"] is None

    assert body["insights"] == []

    assert body["vendor_profile"] == {
        "company_name": "Acme Corp",
        "contact_name": "Jane Doe",
        "contact_role": "CTO",
    }

    updated_at = datetime.fromisoformat(body["updated_at"])
    assert updated_at.utcoffset() == timezone.utc.utcoffset(None)
    assert abs((datetime.now(timezone.utc) - updated_at).total_seconds()) < 60


def test_seeded_interview_state(client):
    state = _seed_interview()
    state.status = "active"
    state.current_node_id = "ai_ml_depth"
    state.scout_findings.append(
        ScoutFinding(topic="reputation", summary="Solid reviews.", source_url="https://example.com/reviews")
    )
    state.scout_findings.append(ScoutFinding(topic="news", summary="No recent press.", source_url=None))

    response = client.get(_url(state.interview_id))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "active"
    assert body["current_topic"] == "ai_ml_capability"
    assert body["pipeline_status"] is None
    assert body["scorecard"] is None
    assert body["recommendation"] is None

    assert body["insights"] == [
        {"topic": "reputation", "summary": "Solid reviews.", "source_url": "https://example.com/reviews"},
        {"topic": "news", "summary": "No recent press.", "source_url": None},
    ]


def test_state_reflects_pipeline_progress(client):
    from app.services.evaluator_agent import STATUS_THRESHOLD
    from app.services.interview_config import get_rubric

    state = _seed_interview()
    state.status = "finished"
    state.pipeline_status = "ready"

    rubric = get_rubric()
    # Every real shipped category has a DIFFERENT label set, so a uniform
    # literal score can't be used - derive a valid (value, points) pair per
    # category from that category's own value_options (the best/first
    # option), and hand-compute overall from the REAL shipped rubric.yaml
    # weights/points rather than guessing.
    categories = [
        CategoryScore(
            id=c.id,
            name=c.name,
            weight=c.weight,
            value=c.value_options[0].label,
            points=c.value_options[0].points,
            evidence=["ev1"],
        )
        for c in rubric.values()
    ]
    # All 7 shipped categories' first (best) value_option is worth 100 points,
    # and the shipped weights already sum to 1.0, so:
    # overall = 100 * (0.20+0.20+0.15+0.15+0.10+0.10+0.10) = 100 * 1.0 = 100.0
    total_weight = sum(c.weight for c in categories)
    overall = round(sum(c.points * c.weight for c in categories) / total_weight, 1)
    assert overall == 100.0
    status = "APPROVED" if overall >= STATUS_THRESHOLD else "REJECTED"
    scorecard = Scorecard(categories=categories, overall=overall, status=status)
    state.scorecard = scorecard
    state.recommendation = FollowupRecommendation(
        kind="advance",
        reason="Overall score 100/100 meets the advance threshold of 70; a next-round deep-dive is warranted.",
        focus_categories=["interest"],
    )

    response = client.get(_url(state.interview_id))

    assert response.status_code == 200
    body = response.json()
    assert body["pipeline_status"] == "ready"
    assert body["scorecard"] == dataclasses.asdict(scorecard)
    assert body["recommendation"] == dataclasses.asdict(state.recommendation)


def test_end_node_has_null_topic(client):
    state = _seed_interview()
    state.status = "finished"
    state.current_node_id = "END"

    response = client.get(_url(state.interview_id))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "finished"
    assert body["current_topic"] is None


def _chat_url(interview_id: str) -> str:
    return f"/api/interview/{interview_id}/chat"


def _fake_handle_turn(monkeypatch, reply="Great, thanks.", next_node_id=None):
    """Mirrors the real handle_turn's observable side effects (appends both
    turns, may advance current_node_id) without a live Gemini call - matches
    the mocking pattern used by tests/routers/test_llm_gateway.py."""
    calls = []

    async def fake(state, user_text, questionnaire, rubric, mode="avatar"):
        calls.append(
            {"state": state, "user_text": user_text, "questionnaire": questionnaire, "rubric": rubric, "mode": mode}
        )
        state.turns.append(TranscriptTurn(role="candidate", text=user_text))
        state.turns.append(TranscriptTurn(role="interviewer", text=reply))
        if next_node_id is not None:
            state.current_node_id = next_node_id
        return TurnResult(reply=reply, answer_complete=False, completed_question=None, answer_text="")

    monkeypatch.setattr(host_agent, "handle_turn", fake)
    return calls


def test_chat_happy_path_appends_turns_and_not_done(client, monkeypatch):
    state = _seed_interview()
    calls = _fake_handle_turn(monkeypatch, reply="Thanks, tell me more.", next_node_id="ai_ml_capability")

    response = client.post(_chat_url(state.interview_id), json={"text": "We build ML pipelines."})

    assert response.status_code == 200
    assert response.json() == {"reply": "Thanks, tell me more.", "done": False}

    assert len(calls) == 1
    assert calls[0]["user_text"] == "We build ML pipelines."
    # The chat route always drives the Host in chat mode - never the avatar
    # default - so terse typed answers are treated as complete, not pressed
    # for elaboration.
    assert calls[0]["mode"] == "chat"

    assert [(t.role, t.text) for t in state.turns] == [
        ("candidate", "We build ML pipelines."),
        ("interviewer", "Thanks, tell me more."),
    ]


def test_chat_done_true_when_turn_lands_on_end(client, monkeypatch):
    state = _seed_interview()
    _fake_handle_turn(monkeypatch, reply="Thanks for your time!", next_node_id=host_agent.END_NODE_ID)

    response = client.post(_chat_url(state.interview_id), json={"text": "That's everything."})

    assert response.status_code == 200
    assert response.json() == {"reply": "Thanks for your time!", "done": True}


def test_chat_unknown_interview_404(client, monkeypatch):
    calls = _fake_handle_turn(monkeypatch)

    response = client.post(_chat_url("nope"), json={"text": "hello"})

    assert response.status_code == 404
    assert calls == []


def test_chat_empty_text_400(client, monkeypatch):
    state = _seed_interview()
    calls = _fake_handle_turn(monkeypatch)

    response = client.post(_chat_url(state.interview_id), json={"text": "   "})

    assert response.status_code == 400
    assert calls == []


def test_chat_transitions_status_from_created_to_active(client, monkeypatch):
    state = _seed_interview()
    assert state.status == "created"
    _fake_handle_turn(monkeypatch)

    response = client.post(_chat_url(state.interview_id), json={"text": "Hi there."})

    assert response.status_code == 200
    assert state.status == "active"


def test_chat_leaves_active_status_unchanged(client, monkeypatch):
    state = _seed_interview()
    state.status = "active"
    _fake_handle_turn(monkeypatch)

    response = client.post(_chat_url(state.interview_id), json={"text": "Hi there."})

    assert response.status_code == 200
    assert state.status == "active"


@respx.mock
def test_chat_route_gemini_request_includes_chat_mode_prompt(client, patch_settings):
    # Integration: real handle_turn (not monkeypatched) drives the actual
    # Gemini request; the chat route must pass mode="chat" so the terse-answer
    # instructions land in the system prompt sent to Gemini.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    from app.config import settings

    content = json.dumps({"reply": "Got it, thanks.", "answer_complete": True})
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    )
    state = _seed_interview()

    response = client.post(_chat_url(state.interview_id), json={"text": "GCP, done."})

    assert response.status_code == 200
    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert settings.host_chat_mode_prompt in system_content


@respx.mock
def test_avatar_gateway_gemini_request_excludes_chat_mode_prompt(client, patch_settings):
    # Contrast case: the same real handle_turn, driven through the avatar
    # gateway (default mode), must NOT carry the chat-mode text.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    from app.config import settings

    content = json.dumps({"reply": "Great, thanks.", "answer_complete": True})
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    )
    state = _seed_interview()

    response = client.post(
        f"/llm/{state.interview_id}/v1/chat/completions",
        json={
            "model": "resonance-host",
            "stream": False,
            "messages": [{"role": "user", "content": "GCP, done."}],
        },
        headers={"Authorization": f"Bearer {state.gateway_token}"},
    )

    assert response.status_code == 200
    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert settings.host_chat_mode_prompt not in system_content


def _profile_url(interview_id: str) -> str:
    return f"/api/interview/{interview_id}/profile"


def test_patch_profile_partial_update_only_touches_provided_fields(client):
    state = _seed_interview()

    response = client.patch(_profile_url(state.interview_id), json={"company_name": "New Co"})

    assert response.status_code == 200
    body = response.json()
    assert body["vendor_profile"] == {
        "company_name": "New Co",
        "contact_name": "Jane Doe",
        "contact_role": "CTO",
    }
    assert body["manually_edited_fields"] == ["company_name"]

    assert state.vendor_profile.company_name == "New Co"
    assert state.vendor_profile.contact_name == "Jane Doe"
    assert state.vendor_profile.contact_role == "CTO"


def test_patch_profile_sorts_manually_edited_fields(client):
    state = _seed_interview()

    response = client.patch(
        _profile_url(state.interview_id),
        json={"contact_role": "VP Eng", "company_name": "Acme Corp"},
    )

    assert response.status_code == 200
    # Sorted alphabetically regardless of request key order.
    assert response.json()["manually_edited_fields"] == ["company_name", "contact_role"]


def test_patch_profile_unknown_interview_404(client):
    response = client.patch(_profile_url("nope"), json={"company_name": "New Co"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown interview"


def test_patch_profile_after_finalize_409(client):
    state = _seed_interview()
    state.pipeline_status = "interviewed"

    response = client.patch(_profile_url(state.interview_id), json={"company_name": "New Co"})

    assert response.status_code == 409
    assert response.json()["detail"] == "Interview already finalized"
    # Rejected before any mutation - the profile is untouched.
    assert state.vendor_profile.company_name == "Acme Corp"
    assert state.manually_edited_fields == set()


def test_patch_profile_empty_body_400(client):
    state = _seed_interview()

    response = client.patch(_profile_url(state.interview_id), json={})

    assert response.status_code == 400


def test_patch_profile_all_fields_none_400(client):
    state = _seed_interview()

    response = client.patch(
        _profile_url(state.interview_id),
        json={"company_name": None, "contact_name": None, "contact_role": None},
    )

    assert response.status_code == 400


def test_patch_profile_appends_system_note_turn_with_old_and_new_values(client):
    state = _seed_interview()

    response = client.patch(
        _profile_url(state.interview_id),
        json={"company_name": "Acme Corp International", "contact_role": "VP Eng"},
    )

    assert response.status_code == 200
    assert len(state.turns) == 1
    note_turn = state.turns[0]
    assert note_turn.role == "system"
    assert '"Acme Corp"' in note_turn.text
    assert '"Acme Corp International"' in note_turn.text
    assert '"CTO"' in note_turn.text
    assert '"VP Eng"' in note_turn.text
    assert note_turn.text.startswith("[Vendor manually corrected their profile:")


def test_patch_profile_no_note_turn_when_value_unchanged(client):
    state = _seed_interview()

    response = client.patch(_profile_url(state.interview_id), json={"company_name": "Acme Corp"})

    assert response.status_code == 200
    # No-op PATCH (value equals current) must not append a note turn...
    assert state.turns == []
    # ...but the field still locks.
    assert response.json()["manually_edited_fields"] == ["company_name"]


def test_patch_profile_clearing_contact_role_sets_none(client):
    state = _seed_interview()

    response = client.patch(_profile_url(state.interview_id), json={"contact_role": ""})

    assert response.status_code == 200
    assert response.json()["vendor_profile"]["contact_role"] is None
    assert state.vendor_profile.contact_role is None
    assert len(state.turns) == 1
    assert '"CTO"' in state.turns[0].text
    assert "(not set)" in state.turns[0].text


def test_patch_profile_clearing_company_name_sets_empty_string(client):
    state = _seed_interview()

    response = client.patch(_profile_url(state.interview_id), json={"company_name": "   "})

    assert response.status_code == 200
    assert response.json()["vendor_profile"]["company_name"] == ""
    assert state.vendor_profile.company_name == ""


@respx.mock
def test_patch_profile_locks_field_against_subsequent_llm_profile_updates(client, patch_settings):
    # End-to-end-ish: after manually correcting company_name, a chat turn
    # whose mocked Gemini response reports a conflicting profile_updates for
    # that same field must NOT overwrite the manual value.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    state = _seed_interview()

    patch_response = client.patch(_profile_url(state.interview_id), json={"company_name": "Manually Corrected Co"})
    assert patch_response.status_code == 200

    content = json.dumps(
        {
            "reply": "Got it, thanks.",
            "answer_complete": True,
            "profile_updates": {
                "company_name": "LLM Reported Co",
                "contact_name": None,
                "contact_role": None,
            },
        }
    )
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    )

    chat_response = client.post(_chat_url(state.interview_id), json={"text": "Actually we're LLM Reported Co now."})

    assert chat_response.status_code == 200
    assert state.vendor_profile.company_name == "Manually Corrected Co"


# --- Start-screen intake: profile pre-fill + about-text on POST /api/interview ---


def _gemini_summary_response(bullets: str = "- Builds AI tooling for banks") -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": bullets}}]})


def test_create_interview_with_intake_prefills_and_locks_profile(client):
    response = client.post(
        "/api/interview",
        json={"contact_name": "Sam Lee", "contact_role": "CTO", "company_name": "Acme"},
    )

    assert response.status_code == 200
    state = interview_state.get(response.json()["interview_id"])
    assert state.vendor_profile == VendorProfile(
        company_name="Acme", contact_name="Sam Lee", contact_role="CTO"
    )
    # Intake values lock exactly like a manual PATCH edit - the Host's
    # LLM-reported profile_updates can never overwrite what the vendor typed.
    assert state.manually_edited_fields == {"contact_name", "contact_role", "company_name"}
    # No about_text -> no summarization call, no context.
    assert state.vendor_context == ""


def test_create_interview_blank_intake_fields_are_ignored(client):
    response = client.post("/api/interview", json={"contact_name": "  ", "company_name": None})

    state = interview_state.get(response.json()["interview_id"])
    assert state.vendor_profile == VendorProfile()
    assert state.manually_edited_fields == set()


@respx.mock
def test_create_interview_about_text_summarized_into_context(client, patch_settings):
    patch_settings(gemini_api_key="gem-key")
    route = respx.post(CHAT_URL).mock(return_value=_gemini_summary_response())

    response = client.post(
        "/api/interview",
        json={"contact_name": "Sam", "company_name": "Acme", "about_text": "We build AI tooling for banks."},
    )

    assert response.status_code == 200
    state = interview_state.get(response.json()["interview_id"])
    assert state.vendor_context == "- Builds AI tooling for banks"
    sent = json.loads(route.calls[0].request.content)
    assert "We build AI tooling for banks." in sent["messages"][1]["content"]


@respx.mock
def test_create_interview_about_text_summary_soft_fails(client, patch_settings):
    # A summarization hiccup must never block interview creation.
    patch_settings(gemini_api_key="gem-key")
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500))

    response = client.post("/api/interview", json={"about_text": "We build things."})

    assert response.status_code == 200
    state = interview_state.get(response.json()["interview_id"])
    assert state.vendor_context == ""


def test_create_interview_builds_full_question_plan(client):
    response = client.post("/api/interview", json={"domain": "frontier_tech"})

    state = interview_state.get(response.json()["interview_id"])
    assert state.question_plan[0] == "theme_interest"
    assert state.question_plan[-1] == "closing"
    assert state.current_node_id == "theme_interest"


def test_create_prod_interview_short_duration_gets_topk_plan(client, patch_settings):
    patch_settings(prod_avatar_id="avatar-1", demo_passcode="sesame")

    response = client.post(
        "/api/interview",
        json={"domain": "frontier_tech", "tier": "prod", "passcode": "sesame", "duration_minutes": 3},
    )

    assert response.status_code == 200
    state = interview_state.get(response.json()["interview_id"])
    # 180s -> the four heaviest rubric categories in script order + closing.
    assert state.question_plan == [
        "theme_interest",
        "existing_offerings",
        "tech_maturity",
        "connectivity",
        "closing",
    ]


# --- Intake documents: POST /api/interview/{id}/document ---


def _upload(client, interview_id: str, filename: str, content: bytes):
    return client.post(
        f"/api/interview/{interview_id}/document",
        files={"file": (filename, content, "application/octet-stream")},
    )


@respx.mock
def test_upload_txt_document_appends_context(client, patch_settings):
    patch_settings(gemini_api_key="gem-key")
    respx.post(CHAT_URL).mock(return_value=_gemini_summary_response("- Ships data pipelines"))
    state = _seed_interview()
    state.vendor_context = "- From the about box"

    response = _upload(client, state.interview_id, "profile.txt", b"We ship data pipelines for banks.")

    assert response.status_code == 200
    assert response.json() == {"filename": "profile.txt", "word_count": 6, "truncated": False}
    # Document bullets append to (never replace) earlier intake material.
    assert state.vendor_context == "- From the about box\n- Ships data pipelines"


@respx.mock
def test_upload_document_trims_to_word_limit(client, patch_settings):
    patch_settings(gemini_api_key="gem-key")
    route = respx.post(CHAT_URL).mock(return_value=_gemini_summary_response())
    state = _seed_interview()
    content = ("word " * 3500).encode()

    response = _upload(client, state.interview_id, "deck.txt", content)

    assert response.status_code == 200
    body = response.json()
    assert body["word_count"] == 3500
    assert body["truncated"] is True
    # Only the first 3,000 words ever reach the summarizer.
    sent = json.loads(route.calls[0].request.content)
    assert len(sent["messages"][1]["content"].split()) == 3000


@respx.mock
def test_upload_docx_document_parses(client, patch_settings):
    import io

    import docx

    patch_settings(gemini_api_key="gem-key")
    respx.post(CHAT_URL).mock(return_value=_gemini_summary_response("- Builds rockets"))
    state = _seed_interview()
    document = docx.Document()
    document.add_paragraph("Acme builds reusable rockets for cargo.")
    buffer = io.BytesIO()
    document.save(buffer)

    response = _upload(client, state.interview_id, "company.docx", buffer.getvalue())

    assert response.status_code == 200
    assert response.json()["word_count"] == 6
    assert state.vendor_context == "- Builds rockets"


def test_upload_unsupported_document_type_400(client):
    state = _seed_interview()
    response = _upload(client, state.interview_id, "notes.exe", b"binary")
    assert response.status_code == 400


def test_upload_unreadable_document_400(client):
    # A .pdf that isn't a PDF at all -> parser failure -> 400, not a 500.
    state = _seed_interview()
    response = _upload(client, state.interview_id, "fake.pdf", b"not a pdf")
    assert response.status_code == 400


def test_upload_empty_document_400(client):
    state = _seed_interview()
    response = _upload(client, state.interview_id, "empty.txt", b"   ")
    assert response.status_code == 400


def test_upload_document_unknown_interview_404(client):
    response = _upload(client, "nope", "profile.txt", b"hello world")
    assert response.status_code == 404


def test_upload_document_after_finalize_409(client):
    state = _seed_interview()
    state.pipeline_status = "interviewed"
    response = _upload(client, state.interview_id, "profile.txt", b"hello world")
    assert response.status_code == 409


def test_upload_oversize_document_413(client):
    state = _seed_interview()
    response = _upload(client, state.interview_id, "big.txt", b"x" * (10 * 1024 * 1024 + 1))
    assert response.status_code == 413


@respx.mock
def test_upload_document_summary_soft_fails(client, patch_settings):
    # The upload still succeeds when summarization fails - the context just
    # goes unenriched.
    patch_settings(gemini_api_key="gem-key")
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500))
    state = _seed_interview()

    response = _upload(client, state.interview_id, "profile.txt", b"We ship data pipelines.")

    assert response.status_code == 200
    assert state.vendor_context == ""
