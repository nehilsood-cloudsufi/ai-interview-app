import dataclasses
from datetime import datetime, timezone

from app.models import TranscriptTurn
from app.services import host_agent, interview_state
from app.services.coordinator_agent import FollowupRecommendation
from app.services.evaluator_agent import CategoryScore, Scorecard
from app.services.host_agent import TurnResult
from app.services.interview_state import ScoutFinding, VendorProfile


def _seed_interview(domain="ai_ml"):
    return interview_state.create(
        VendorProfile(
            company_name="Acme Corp",
            website="https://acme.example",
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
    assert state.domain == "ai_ml"


def test_create_interview_with_null_domain_defaults_domain(client):
    response = client.post("/api/interview", json={"domain": None})

    assert response.status_code == 200
    state = interview_state.get(response.json()["interview_id"])
    assert state.domain == "ai_ml"


def test_create_interview_with_valid_domain(client):
    response = client.post("/api/interview", json={"domain": "cloud_infrastructure"})

    assert response.status_code == 200
    state = interview_state.get(response.json()["interview_id"])
    assert state.domain == "cloud_infrastructure"


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
    assert set(ids) == {"ai_ml", "cloud_infrastructure", "data_engineering"}
    for entry in body["domains"]:
        assert entry["title"]


def test_create_interview_id_resolves_via_get_state(client):
    response = client.post("/api/interview")
    interview_id = response.json()["interview_id"]

    state_response = client.get(_url(interview_id))

    assert state_response.status_code == 200
    body = state_response.json()
    assert body["status"] == "created"
    # The start node is the questionnaire's first question (intro), which
    # onboards the vendor conversationally now that the intake form is gone.
    assert body["current_topic"] == "onboarding"


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
    # The start node is the questionnaire's first question (intro).
    assert body["current_topic"] == "onboarding"

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
        "website": "https://acme.example",
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
    from app.services.interview_config import get_rubric

    state = _seed_interview()
    state.status = "finished"
    state.pipeline_status = "ready"
    scorecard = Scorecard(
        categories=[
            CategoryScore(id=c.id, name=c.name, weight=c.weight, score=4.0, evidence=["ev1"])
            for c in get_rubric().values()
        ],
        overall=4.0,
    )
    state.scorecard = scorecard
    state.recommendation = FollowupRecommendation(
        kind="advance", reason="Overall score 4/5 meets the advance threshold.", focus_categories=["experience"]
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

    async def fake(state, user_text, questionnaire, rubric):
        calls.append({"state": state, "user_text": user_text, "questionnaire": questionnaire, "rubric": rubric})
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
