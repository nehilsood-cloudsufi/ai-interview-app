import httpx
import respx

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


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
