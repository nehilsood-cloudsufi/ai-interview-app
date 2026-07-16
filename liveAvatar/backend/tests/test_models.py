import pytest
from pydantic import ValidationError

from app.models import (
    ConcurrencyResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    FinalizeTranscriptRequest,
    FinalizeTranscriptResponse,
    StopSessionRequest,
    StopSessionResponse,
    TranscriptTurn,
    UploadResumeResponse,
)


def test_create_session_request_all_optional():
    req = CreateSessionRequest()
    assert req.context_id is None
    assert req.llm_configuration_id is None
    assert req.avatar_id is None
    assert req.api_key is None


def test_create_session_response_requires_fields():
    with pytest.raises(ValidationError):
        CreateSessionResponse(session_token="tok")
    resp = CreateSessionResponse(session_token="tok", session_id="sid")
    assert resp.session_token == "tok"
    assert resp.session_id == "sid"


def test_stop_session_request_all_optional():
    req = StopSessionRequest()
    assert req.session_token is None
    assert req.context_id is None
    assert req.api_key is None


def test_stop_session_response_defaults():
    resp = StopSessionResponse(status="stopped")
    assert resp.api_status is None


def test_upload_resume_response_requires_context_id():
    with pytest.raises(ValidationError):
        UploadResumeResponse()
    resp = UploadResumeResponse(context_id="ctx-1")
    assert resp.context_id == "ctx-1"


def test_concurrency_response():
    resp = ConcurrencyResponse(active_sessions=3)
    assert resp.active_sessions == 3


def test_transcript_turn_valid_roles():
    turn = TranscriptTurn(role="interviewer", text="hi")
    assert turn.timestamp is None
    turn2 = TranscriptTurn(role="candidate", text="hello", timestamp=1.5)
    assert turn2.timestamp == 1.5


def test_transcript_turn_rejects_invalid_role():
    with pytest.raises(ValidationError):
        TranscriptTurn(role="moderator", text="hi")


def test_finalize_transcript_request():
    req = FinalizeTranscriptRequest(
        session_id="s1",
        turns=[{"role": "interviewer", "text": "hi"}],
    )
    assert req.session_id == "s1"
    assert len(req.turns) == 1
    assert isinstance(req.turns[0], TranscriptTurn)


def test_finalize_transcript_response_default_summary_ok():
    resp = FinalizeTranscriptResponse(summary="summary text")
    assert resp.summary_ok is True
