from typing import Literal

from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    context_id: str | None = None
    llm_configuration_id: str | None = None
    avatar_id: str | None = None
    api_key: str | None = None
    # Set (with PUBLIC_BASE_URL configured) to run in gateway mode: the session
    # gets a per-interview Custom LLM pointing back at our /llm/{id}/v1 gateway.
    interview_id: str | None = None


class CreateSessionResponse(BaseModel):
    session_token: str
    session_id: str


class StopSessionRequest(BaseModel):
    session_token: str | None = None
    context_id: str | None = None
    api_key: str | None = None
    # Gateway mode: identifies the interview whose LLM config/secret/context
    # should be torn down best-effort on stop.
    interview_id: str | None = None


class StopSessionResponse(BaseModel):
    status: str
    api_status: int | None = None


class UploadResumeResponse(BaseModel):
    context_id: str


class VendorProfileResponse(BaseModel):
    interview_id: str


class ConcurrencyResponse(BaseModel):
    active_sessions: int


class TranscriptTurn(BaseModel):
    role: Literal["interviewer", "candidate"]
    text: str
    timestamp: float | None = None


class FinalizeTranscriptRequest(BaseModel):
    session_id: str
    turns: list[TranscriptTurn]


class FinalizeTranscriptResponse(BaseModel):
    summary: str
    # False when summary generation failed but the transcript was still saved.
    summary_ok: bool = True


class CategoryScoreModel(BaseModel):
    id: str
    name: str
    weight: float
    score: float | None
    evidence: list[str]


class ScorecardModel(BaseModel):
    categories: list[CategoryScoreModel]
    overall: float | None
    answered_questions: int


class ScoutFindingModel(BaseModel):
    topic: str
    summary: str
    source_url: str | None


class InterviewStateResponse(BaseModel):
    status: Literal["created", "active", "finished"]
    # Topic of the current questionnaire node; None when the interview has
    # reached END (or the node id is unknown).
    current_topic: str | None
    scorecard: ScorecardModel
    insights: list[ScoutFindingModel]
    updated_at: str  # ISO-8601 UTC timestamp of this snapshot
