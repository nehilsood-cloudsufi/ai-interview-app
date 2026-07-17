import httpx
import respx

from app.services import interview_state
from app.services.interview_state import AnswerScore, ScoutFinding, VendorProfile

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Keys of a legacy (non-gateway) finalize record - the saved shape must stay
# byte-compatible when no interview_id is involved.
LEGACY_RECORD_KEYS = {"session_id", "created_at", "turns", "summary", "summary_ok"}


def _seed_interview(scores=None, findings=None):
    state = interview_state.create(
        VendorProfile(
            company_name="Acme Corp",
            website="https://acme.example",
            contact_name="Jane Doe",
            contact_role="CTO",
            doc_text="a huge vendor document that must never be persisted",
        )
    )
    state.scores.extend(scores or [])
    state.scout_findings.extend(findings or [])
    return state


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
def test_finalize_with_interview_embeds_enrichment(client, patch_settings, tmp_transcripts_dir):
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
    state = _seed_interview(
        scores=[
            AnswerScore(question_id="company_overview", category_scores={"experience": 4}, evidence="ev1", rationale="r1"),
        ],
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
    assert scorecard["answered_questions"] == 1
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


def test_finalize_summary_failure_still_saves_enriched_record(client, patch_settings, tmp_transcripts_dir):
    # No Gemini key -> generate_summary raises -> summary soft-fails, but the
    # enriched record (vendor profile, scorecard, findings) must still be saved.
    patch_settings(gemini_api_key=None, transcripts_local_dir=str(tmp_transcripts_dir), gcs_bucket=None)
    state = _seed_interview(
        scores=[
            AnswerScore(question_id="company_overview", category_scores={"experience": 3}, evidence="ev", rationale="r"),
        ],
    )

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
