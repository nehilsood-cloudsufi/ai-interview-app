import httpx
import respx

from app.models import TranscriptTurn
from app.services import interview_state, pipeline
from app.services.interview_state import VendorProfile

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


def _stub_enqueue(monkeypatch):
    """Replace pipeline.enqueue with a no-op that records its calls; the
    pipeline's own Scout -> Evaluator -> Coordinator behavior is covered by
    tests/services/test_pipeline.py, not here."""
    calls = []

    def _fake(state, session_id, payload):
        calls.append({"state": state, "session_id": session_id, "payload": payload})

    monkeypatch.setattr(pipeline, "enqueue", _fake)
    return calls


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
    # No interview_id -> no pipeline at all.
    assert body["pipeline_status"] is None

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
def test_finalize_with_interview_returns_fast_and_enqueues_pipeline(
    client, patch_settings, tmp_transcripts_dir, monkeypatch
):
    # Gateway mode: finalize no longer runs the evaluator/coordinator inline -
    # it saves an "interviewed" record and hands off to the background
    # pipeline. The pipeline's own Scout -> Evaluator -> Coordinator behavior
    # is covered by tests/services/test_pipeline.py, not here.
    patch_settings(
        gemini_api_key="gem-key",
        gemini_base_url=GEMINI_BASE_URL,
        transcripts_local_dir=str(tmp_transcripts_dir),
        gcs_bucket=None,
    )
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "Nice summary"}}]})
    )
    calls = _stub_enqueue(monkeypatch)
    state = _seed_interview()

    response = client.post(
        "/api/transcript/finalize",
        json={
            "session_id": "e1",
            "interview_id": state.interview_id,
            "turns": [{"role": "candidate", "text": "hello"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == "Nice summary"
    assert body["summary_ok"] is True
    assert body["pipeline_status"] == "interviewed"
    # Scorecard/insights/recommendation arrive later via polling, never here.
    assert body["scorecard"] is None
    assert body["insights"] is None
    assert body["recommendation"] is None

    # The interview is marked finished and handed to the pipeline exactly once.
    assert state.status == "finished"
    assert state.pipeline_status == "interviewed"
    assert len(calls) == 1
    assert calls[0]["state"] is state
    assert calls[0]["session_id"] == "e1"
    assert calls[0]["payload"]["pipeline_status"] == "interviewed"

    saved = client.get("/api/transcript/e1").json()
    # Vendor profile is embedded WITHOUT the (potentially huge) doc_text.
    assert saved["vendor_profile"] == {
        "company_name": "Acme Corp",
        "website": "https://acme.example",
        "contact_name": "Jane Doe",
        "contact_role": "CTO",
    }
    assert saved["pipeline_status"] == "interviewed"
    assert saved["summary"] == "Nice summary"
    assert saved["summary_ok"] is True


def test_finalize_summary_failure_still_enqueues_pipeline(
    client, patch_settings, tmp_transcripts_dir, monkeypatch
):
    # No Gemini key -> generate_summary raises -> summary soft-fails, but the
    # interview must still be marked finished and handed to the pipeline -
    # the summary and pipeline handoff fail independently.
    patch_settings(gemini_api_key=None, transcripts_local_dir=str(tmp_transcripts_dir), gcs_bucket=None)
    calls = _stub_enqueue(monkeypatch)
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
    assert body["pipeline_status"] == "interviewed"

    assert state.status == "finished"
    assert len(calls) == 1

    saved = client.get("/api/transcript/e2").json()
    assert saved["summary_ok"] is False
    assert saved["vendor_profile"]["company_name"] == "Acme Corp"
    assert saved["pipeline_status"] == "interviewed"


@respx.mock
def test_finalize_double_finalize_short_circuits_and_returns_saved_summary(
    client, patch_settings, tmp_transcripts_dir, monkeypatch
):
    # A retried finalize call (or the UI double-submitting) must not
    # re-enqueue the pipeline or re-save over a record the pipeline may
    # already be mutating in the background - but it also must not hand back
    # a lossy empty summary; it should reflect what was actually saved.
    patch_settings(
        gemini_api_key="gem-key",
        gemini_base_url=GEMINI_BASE_URL,
        transcripts_local_dir=str(tmp_transcripts_dir),
        gcs_bucket=None,
    )
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "Nice summary"}}]})
    )
    calls = _stub_enqueue(monkeypatch)

    save_calls: list = []
    from app.services import transcript_store

    real_save = transcript_store.save

    async def _tracking_save(session_id, payload):
        save_calls.append(session_id)
        await real_save(session_id, payload)

    monkeypatch.setattr(transcript_store, "save", _tracking_save)

    state = _seed_interview()
    body = {
        "session_id": "e3",
        "interview_id": state.interview_id,
        "turns": [{"role": "candidate", "text": "hello"}],
    }

    first = client.post("/api/transcript/finalize", json=body)
    assert first.status_code == 200
    assert first.json()["pipeline_status"] == "interviewed"
    assert len(calls) == 1
    assert len(save_calls) == 1

    # Simulate the pipeline having advanced past "interviewed" in the
    # background before the (retried) second finalize call arrives.
    state.pipeline_status = "ready"

    second = client.post("/api/transcript/finalize", json=body)

    assert second.status_code == 200
    assert second.json() == {
        "summary": "Nice summary",
        "summary_ok": True,
        "pipeline_status": "ready",
        "scorecard": None,
        "insights": None,
        "recommendation": None,
    }
    # No second enqueue, no second save.
    assert len(calls) == 1
    assert len(save_calls) == 1


def test_finalize_double_finalize_short_circuit_without_saved_record_falls_back_to_empty(
    client, patch_settings, tmp_transcripts_dir, monkeypatch
):
    # If, for whatever reason, no record was ever persisted (or the lookup
    # itself fails), the short-circuit response falls back to the legacy
    # empty-summary shape rather than raising.
    patch_settings(gemini_api_key=None, transcripts_local_dir=str(tmp_transcripts_dir), gcs_bucket=None)

    state = _seed_interview()
    state.pipeline_status = "scouting"  # claimed by a finalize that never got this far to save

    response = client.post(
        "/api/transcript/finalize",
        json={
            "session_id": "no-record",
            "interview_id": state.interview_id,
            "turns": [{"role": "candidate", "text": "hello"}],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "summary": "",
        "summary_ok": True,
        "pipeline_status": "scouting",
        "scorecard": None,
        "insights": None,
        "recommendation": None,
    }


def test_finalize_failed_save_resets_claim_so_retry_proceeds(
    client, patch_settings, tmp_transcripts_dir, monkeypatch
):
    # A finalize that fails to save must reset state.pipeline_status back to
    # None so the claim doesn't permanently lock out a retry - the retried
    # call should regenerate the summary and re-enqueue the pipeline.
    patch_settings(gemini_api_key=None, transcripts_local_dir=str(tmp_transcripts_dir), gcs_bucket=None)
    calls = _stub_enqueue(monkeypatch)

    from app.services import transcript_store

    real_save = transcript_store.save
    should_fail = {"value": True}

    async def _flaky_save(session_id, payload):
        if should_fail["value"]:
            raise OSError("disk full")
        await real_save(session_id, payload)

    monkeypatch.setattr(transcript_store, "save", _flaky_save)

    state = _seed_interview()
    body = {
        "session_id": "e4",
        "interview_id": state.interview_id,
        "turns": [{"role": "candidate", "text": "hello"}],
    }

    first = client.post("/api/transcript/finalize", json=body)
    assert first.status_code == 500
    # The claim must be released, not left as "interviewed" - otherwise the
    # retry below would short-circuit into the idempotency guard instead of
    # actually retrying.
    assert state.pipeline_status is None
    assert len(calls) == 0

    should_fail["value"] = False
    second = client.post("/api/transcript/finalize", json=body)

    assert second.status_code == 200
    assert second.json()["pipeline_status"] == "interviewed"
    assert state.pipeline_status == "interviewed"
    assert len(calls) == 1


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
