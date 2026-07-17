from datetime import datetime, timezone

from app.services import interview_state
from app.services.interview_state import AnswerScore, ScoutFinding, VendorProfile

# Shipped rubric (data/rubric.yaml), in file order.
RUBRIC_META = [
    ("experience", "Experience & Track Record", 0.3),
    ("capability", "Technical Capability", 0.3),
    ("delivery", "Delivery & Team", 0.2),
    ("credibility", "Credibility & Security", 0.2),
]


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

    scorecard = body["scorecard"]
    assert [(c["id"], c["name"], c["weight"]) for c in scorecard["categories"]] == RUBRIC_META
    assert all(c["score"] is None for c in scorecard["categories"])
    assert all(c["evidence"] == [] for c in scorecard["categories"])
    assert scorecard["overall"] is None
    assert scorecard["answered_questions"] == 0

    assert body["insights"] == []

    updated_at = datetime.fromisoformat(body["updated_at"])
    assert updated_at.utcoffset() == timezone.utc.utcoffset(None)
    assert abs((datetime.now(timezone.utc) - updated_at).total_seconds()) < 60


def test_seeded_interview_state(client):
    state = _seed_interview()
    state.status = "active"
    state.current_node_id = "ai_ml_depth"
    state.scores.append(
        AnswerScore(question_id="company_overview", category_scores={"experience": 4}, evidence="ev1", rationale="r1")
    )
    state.scores.append(
        AnswerScore(
            question_id="ai_ml_depth",
            category_scores={"capability": 3, "experience": 5},
            evidence="ev2",
            rationale="r2",
        )
    )
    state.scout_findings.append(
        ScoutFinding(topic="reputation", summary="Solid reviews.", source_url="https://example.com/reviews")
    )
    state.scout_findings.append(ScoutFinding(topic="news", summary="No recent press.", source_url=None))

    response = client.get(_url(state.interview_id))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "active"
    assert body["current_topic"] == "ai_ml_capability"

    scorecard = body["scorecard"]
    by_id = {c["id"]: c for c in scorecard["categories"]}
    assert by_id["experience"]["score"] == 4.5
    assert by_id["experience"]["evidence"] == ["ev1", "ev2"]
    assert by_id["capability"]["score"] == 3.0
    assert by_id["capability"]["evidence"] == ["ev2"]
    assert by_id["delivery"]["score"] is None
    assert by_id["credibility"]["score"] is None
    # Only categories with data count, weights renormalized:
    # (4.5 * 0.3 + 3.0 * 0.3) / 0.6 = 3.75
    assert scorecard["overall"] == 3.75
    assert scorecard["answered_questions"] == 2

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
