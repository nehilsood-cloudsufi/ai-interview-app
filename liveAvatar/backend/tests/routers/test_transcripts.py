import logging

import httpx
import respx

from app.models import TranscriptTurn
from app.services import interview_state
from app.services.appraiser_agent import CategoryScore, Scorecard
from app.services.interview_state import ScoutFinding, VendorProfile

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Keys of a legacy (non-gateway) finalize record - the saved shape must stay
# byte-compatible when no interview_id is involved.
LEGACY_RECORD_KEYS = {"session_id", "created_at", "turns", "summary", "summary_ok"}


def _seed_interview(turns=None, findings=None):
    state = interview_state.create(
        VendorProfile(
            company_name="Acme Corp",
            website="https://acme.example",
            contact_name="Jane Doe",
            contact_role="CTO",
        )
    )
    state.turns.extend(
        turns
        if turns is not None
        else [
            TranscriptTurn(role="interviewer", text="Tell me about your company."),
            TranscriptTurn(role="candidate", text="We build ML pipelines for banks."),
        ]
    )
    state.scout_findings.extend(findings or [])
    return state


def make_scorecard(scores: dict[str, float | None], overall: float | None, evidence=None) -> Scorecard:
    from app.services.interview_config import get_rubric

    return Scorecard(
        categories=[
            CategoryScore(
                id=c.id,
                name=c.name,
                weight=c.weight,
                score=scores.get(c.id),
                evidence=(evidence or {}).get(c.id, []),
            )
            for c in get_rubric().values()
        ],
        overall=overall,
    )


def _patch_score_interview(monkeypatch, scorecard: Scorecard | Exception, calls: list | None = None):
    """Replace the holistic scoring call; its own Gemini plumbing is covered by
    tests/services/test_appraiser_agent.py."""
    from app.services import appraiser_agent

    async def _fake(turns, rubric):
        if calls is not None:
            calls.append(turns)
        if isinstance(scorecard, Exception):
            raise scorecard
        return scorecard

    monkeypatch.setattr(appraiser_agent, "score_interview", _fake)


def test_finalize_empty_turns_returns_400(client, tmp_transcripts_dir):
    response = client.post(
        "/api/transcript/finalize", json={"session_id": "s1", "turns": []}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Transcript has no turns to finalize"


@respx.mock
def test_finalize_summary_failure_still_saves(client, patch_settings, tmp_transcripts_dir):
    # No Gemini key configured -> generate_summary raises -> summary_ok False,
    # but the transcript itself must still be persisted (tolerant behavior).
    patch_settings(gemini_api_key=None, transcripts_local_dir=str(tmp_transcripts_dir), gcs_bucket=None)

    response = client.post(
        "/api/transcript/finalize",
        json={
            "session_id": "s1",
            "turns": [{"role": "candidate", "text": "hello"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary_ok"] is False
    assert body["summary"] == ""

    get_response = client.get("/api/transcript/s1")
    assert get_response.status_code == 200
    saved = get_response.json()
    assert saved["summary_ok"] is False
    assert saved["session_id"] == "s1"


@respx.mock
def test_finalize_happy_path_with_summary(client, patch_settings, tmp_transcripts_dir):
    patch_settings(
        gemini_api_key="gem-key",
        gemini_base_url=GEMINI_BASE_URL,
        transcripts_local_dir=str(tmp_transcripts_dir),
        gcs_bucket=None,
    )
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "Nice summary"}}]}
        )
    )

    turns = [
        {"role": "interviewer", "text": "Tell me about RAG.", "timestamp": 1.0},
        {"role": "candidate", "text": "hello"},
    ]
    response = client.post(
        "/api/transcript/finalize",
        json={"session_id": "s2", "turns": turns},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == "Nice summary"
    assert body["summary_ok"] is True

    # The full record must be persisted: summary, all turns, and a created_at stamp.
    saved = client.get("/api/transcript/s2").json()
    assert saved["session_id"] == "s2"
    assert saved["summary"] == "Nice summary"
    assert saved["summary_ok"] is True
    assert saved["created_at"]  # UTC ISO timestamp is written
    assert [(t["role"], t["text"]) for t in saved["turns"]] == [
        ("interviewer", "Tell me about RAG."),
        ("candidate", "hello"),
    ]


@respx.mock
def test_finalize_and_get_roundtrip_over_gcs(client, patch_settings, fake_gcs_client):
    # Persistence and retrieval must work through the GCS backend, not just
    # local files - finalize writes the blob, GET reads it back.
    patch_settings(gemini_api_key=None, gcs_bucket="my-bucket")

    post = client.post(
        "/api/transcript/finalize",
        json={"session_id": "g1", "turns": [{"role": "candidate", "text": "hi"}]},
    )
    assert post.status_code == 200
    assert "transcripts/g1.json" in fake_gcs_client.buckets["my-bucket"].store

    saved = client.get("/api/transcript/g1")
    assert saved.status_code == 200
    body = saved.json()
    assert body["session_id"] == "g1"
    assert body["created_at"]
    assert body["turns"] == [{"role": "candidate", "text": "hi", "timestamp": None}]


def test_finalize_save_failure_returns_500(client, patch_settings, monkeypatch):
    patch_settings(gemini_api_key=None)

    async def _boom(session_id, payload):
        raise OSError("disk full")

    from app.services import transcript_store

    monkeypatch.setattr(transcript_store, "save", _boom)

    response = client.post(
        "/api/transcript/finalize",
        json={
            "session_id": "s1",
            "turns": [{"role": "candidate", "text": "hello"}],
        },
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to save transcript"


@respx.mock
def test_finalize_with_interview_embeds_enrichment(client, patch_settings, tmp_transcripts_dir, monkeypatch):
    patch_settings(
        gemini_api_key="gem-key",
        gemini_base_url=GEMINI_BASE_URL,
        transcripts_local_dir=str(tmp_transcripts_dir),
        gcs_bucket=None,
    )
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "Nice summary"}}]}
        )
    )
    scoring_calls: list = []
    _patch_score_interview(
        monkeypatch,
        make_scorecard({"experience": 4.0}, overall=4.0, evidence={"experience": ["ev1"]}),
        calls=scoring_calls,
    )
    state = _seed_interview(
        findings=[ScoutFinding(topic="news", summary="No recent press.", source_url=None)],
    )

    response = client.post(
        "/api/transcript/finalize",
        json={
            "session_id": "e1",
            "interview_id": state.interview_id,
            "turns": [{"role": "candidate", "text": "hello"}],
        },
    )

    assert response.status_code == 200

    # Scoring ran once, over the Host's authoritative transcript (state.turns,
    # not the frontend-captured body turns).
    assert scoring_calls == [state.turns]

    saved = client.get("/api/transcript/e1").json()
    # Vendor profile is embedded WITHOUT the (potentially huge) doc_text.
    assert saved["vendor_profile"] == {
        "company_name": "Acme Corp",
        "website": "https://acme.example",
        "contact_name": "Jane Doe",
        "contact_role": "CTO",
    }
    scorecard = saved["scorecard"]
    by_id = {c["id"]: c for c in scorecard["categories"]}
    assert by_id["experience"]["score"] == 4.0
    assert by_id["experience"]["evidence"] == ["ev1"]
    assert scorecard["overall"] == 4.0
    assert saved["scout_findings"] == [
        {"topic": "news", "summary": "No recent press.", "source_url": None}
    ]
    # Legacy fields are untouched.
    assert saved["summary"] == "Nice summary"
    assert saved["summary_ok"] is True

    # The interview is marked finished.
    assert state.status == "finished"

    # The response carries the final scorecard + insights for the UI.
    body = response.json()
    assert body["scorecard"]["overall"] == 4.0
    assert body["insights"] == saved["scout_findings"]


def test_finalize_without_interview_id_keeps_legacy_shape(client, patch_settings, tmp_transcripts_dir):
    patch_settings(gemini_api_key=None, transcripts_local_dir=str(tmp_transcripts_dir), gcs_bucket=None)

    response = client.post(
        "/api/transcript/finalize",
        json={"session_id": "leg1", "turns": [{"role": "candidate", "text": "hi"}]},
    )

    assert response.status_code == 200
    saved = client.get("/api/transcript/leg1").json()
    assert set(saved) == LEGACY_RECORD_KEYS


def test_finalize_unknown_interview_id_treated_as_legacy(client, patch_settings, tmp_transcripts_dir):
    patch_settings(gemini_api_key=None, transcripts_local_dir=str(tmp_transcripts_dir), gcs_bucket=None)

    response = client.post(
        "/api/transcript/finalize",
        json={
            "session_id": "leg2",
            "interview_id": "does-not-exist",
            "turns": [{"role": "candidate", "text": "hi"}],
        },
    )

    assert response.status_code == 200
    saved = client.get("/api/transcript/leg2").json()
    assert set(saved) == LEGACY_RECORD_KEYS


def test_finalize_summary_failure_still_saves_enriched_record(
    client, patch_settings, tmp_transcripts_dir, monkeypatch
):
    # No Gemini key -> generate_summary raises -> summary soft-fails, but the
    # enriched record (vendor profile, scorecard, findings) must still be
    # saved - the summary and scoring calls fail independently.
    patch_settings(gemini_api_key=None, transcripts_local_dir=str(tmp_transcripts_dir), gcs_bucket=None)
    _patch_score_interview(monkeypatch, make_scorecard({"experience": 3.0}, overall=3.0))
    state = _seed_interview()

    response = client.post(
        "/api/transcript/finalize",
        json={
            "session_id": "e2",
            "interview_id": state.interview_id,
            "turns": [{"role": "candidate", "text": "hello"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary_ok"] is False

    saved = client.get("/api/transcript/e2").json()
    assert saved["summary_ok"] is False
    assert saved["vendor_profile"]["company_name"] == "Acme Corp"
    assert saved["scorecard"]["overall"] == 3.0
    assert saved["scout_findings"] == []
    assert state.status == "finished"


def test_finalize_scoring_failure_still_saves_with_null_scorecard(
    client, patch_settings, tmp_transcripts_dir, monkeypatch, caplog
):
    # Holistic scoring blowing up must never lose the transcript: the record
    # is saved with scorecard null, the coordinator is skipped, and a warning
    # is logged.
    patch_settings(gemini_api_key=None, transcripts_local_dir=str(tmp_transcripts_dir), gcs_bucket=None)
    _patch_score_interview(monkeypatch, RuntimeError("scoring exploded"))
    state = _seed_interview()

    with caplog.at_level(logging.WARNING, logger="app.routers.transcripts"):
        response = client.post(
            "/api/transcript/finalize",
            json={
                "session_id": "e3",
                "interview_id": state.interview_id,
                "turns": [{"role": "candidate", "text": "hello"}],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["scorecard"] is None
    assert body["followup"] is None
    assert "Holistic scoring failed" in caplog.text

    saved = client.get("/api/transcript/e3").json()
    assert saved["scorecard"] is None
    assert saved["followup"] is None
    assert saved["vendor_profile"]["company_name"] == "Acme Corp"
    assert state.status == "finished"


def test_finalize_high_scores_includes_followup(client, patch_settings, tmp_transcripts_dir, monkeypatch):
    patch_settings(gemini_api_key=None, transcripts_local_dir=str(tmp_transcripts_dir), gcs_bucket=None)

    from app.services import coordinator_agent
    from app.services.coordinator_agent import FollowupProposal

    # experience 5 -> overall 5.0 >= advance threshold (3.5) -> "advance".
    _patch_score_interview(monkeypatch, make_scorecard({"experience": 5.0}, overall=5.0))

    async def _fake_draft(state, rec, scorecard, rubric):
        assert scorecard.overall == 5.0  # the final scorecard is passed through
        return FollowupProposal(
            recommendation=rec,
            title="Deep dive with Acme Corp",
            agenda=["Technical Capability", "Delivery & Team"],
            duration_minutes=45,
            email_draft="Dear Jane,\n\nLet's schedule a deep dive.",
        )

    monkeypatch.setattr(coordinator_agent, "draft_followup", _fake_draft)

    state = _seed_interview()

    response = client.post(
        "/api/transcript/finalize",
        json={
            "session_id": "f1",
            "interview_id": state.interview_id,
            "turns": [{"role": "candidate", "text": "hello"}],
        },
    )

    assert response.status_code == 200
    followup = response.json()["followup"]
    assert followup["recommendation"]["kind"] == "advance"
    assert followup["recommendation"]["reason"]
    assert followup["title"] == "Deep dive with Acme Corp"
    assert followup["agenda"] == ["Technical Capability", "Delivery & Team"]
    assert followup["duration_minutes"] == 45
    assert followup["email_draft"].startswith("Dear Jane,")

    saved = client.get("/api/transcript/f1").json()
    assert saved["followup"] == followup


def test_finalize_low_scores_has_null_followup(client, patch_settings, tmp_transcripts_dir, monkeypatch):
    patch_settings(gemini_api_key=None, transcripts_local_dir=str(tmp_transcripts_dir), gcs_bucket=None)

    # experience 1 -> overall 1.0 < clarify floor (2.5) -> no recommendation.
    _patch_score_interview(monkeypatch, make_scorecard({"experience": 1.0}, overall=1.0))
    state = _seed_interview()

    response = client.post(
        "/api/transcript/finalize",
        json={
            "session_id": "f2",
            "interview_id": state.interview_id,
            "turns": [{"role": "candidate", "text": "hello"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["followup"] is None

    saved = client.get("/api/transcript/f2").json()
    assert saved["followup"] is None
    # The rest of the enrichment is unaffected.
    assert saved["scorecard"]["overall"] == 1.0


def test_finalize_overall_none_skips_coordinator(client, patch_settings, tmp_transcripts_dir, monkeypatch):
    patch_settings(gemini_api_key=None, transcripts_local_dir=str(tmp_transcripts_dir), gcs_bucket=None)

    from app.services import coordinator_agent

    def _must_not_run(scorecard, rubric):
        raise AssertionError("coordinator must not run when overall is None")

    monkeypatch.setattr(coordinator_agent, "evaluate_followup", _must_not_run)
    _patch_score_interview(monkeypatch, make_scorecard({}, overall=None))
    state = _seed_interview()

    response = client.post(
        "/api/transcript/finalize",
        json={
            "session_id": "f4",
            "interview_id": state.interview_id,
            "turns": [{"role": "candidate", "text": "hello"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["followup"] is None


def test_finalize_coordinator_crash_never_blocks_save(client, patch_settings, tmp_transcripts_dir, monkeypatch):
    patch_settings(gemini_api_key=None, transcripts_local_dir=str(tmp_transcripts_dir), gcs_bucket=None)

    from app.services import coordinator_agent

    def _boom(scorecard, rubric):
        raise RuntimeError("coordinator exploded")

    monkeypatch.setattr(coordinator_agent, "evaluate_followup", _boom)
    _patch_score_interview(monkeypatch, make_scorecard({"experience": 5.0}, overall=5.0))
    state = _seed_interview()

    response = client.post(
        "/api/transcript/finalize",
        json={
            "session_id": "f3",
            "interview_id": state.interview_id,
            "turns": [{"role": "candidate", "text": "hello"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["followup"] is None

    saved = client.get("/api/transcript/f3").json()
    assert saved["followup"] is None
    assert saved["scorecard"]["overall"] == 5.0
    assert state.status == "finished"


def test_get_transcript_not_found_returns_404(client, tmp_transcripts_dir):
    response = client.get("/api/transcript/does-not-exist")
    assert response.status_code == 404
    assert response.json()["detail"] == "Transcript not found"


def test_get_transcript_found(client, tmp_transcripts_dir, patch_settings):
    patch_settings(gemini_api_key=None, transcripts_local_dir=str(tmp_transcripts_dir), gcs_bucket=None)
    client.post(
        "/api/transcript/finalize",
        json={"session_id": "s3", "turns": [{"role": "candidate", "text": "hi"}]},
    )

    response = client.get("/api/transcript/s3")

    assert response.status_code == 200
    assert response.json()["session_id"] == "s3"
