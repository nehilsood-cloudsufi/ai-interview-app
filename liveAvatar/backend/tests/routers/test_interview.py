from datetime import datetime, timezone

from app.services import interview_state
from app.services.interview_state import ScoutFinding, VendorProfile


def _seed_interview():
    return interview_state.create(
        VendorProfile(
            company_name="Acme Corp",
            website="https://acme.example",
            contact_name="Jane Doe",
            contact_role="CTO",
        )
    )


def _url(interview_id: str) -> str:
    return f"/api/interview/{interview_id}/state"


def test_unknown_interview_404(client):
    response = client.get(_url("nope"))
    assert response.status_code == 404


def test_fresh_interview_state(client):
    state = _seed_interview()

    response = client.get(_url(state.interview_id))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "created"
    # The start node is verify_identity; its topic comes from the shipped questionnaire.
    assert body["current_topic"] == "identity_verification"

    # Scoring is a single holistic pass at finalize - the live-state snapshot
    # deliberately carries no scorecard.
    assert "scorecard" not in body

    assert body["insights"] == []

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
    assert "scorecard" not in body

    assert body["insights"] == [
        {"topic": "reputation", "summary": "Solid reviews.", "source_url": "https://example.com/reviews"},
        {"topic": "news", "summary": "No recent press.", "source_url": None},
    ]


def test_end_node_has_null_topic(client):
    state = _seed_interview()
    state.status = "finished"
    state.current_node_id = "END"

    response = client.get(_url(state.interview_id))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "finished"
    assert body["current_topic"] is None
