from typing import Literal

from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    avatar_id: str | None = None
    # Identifies the interview whose gateway session should be created; the
    # session gets a per-interview Custom LLM pointing back at our
    # /llm/{id}/v1 gateway.
    interview_id: str | None = None


class StopSessionRequest(BaseModel):
    session_token: str | None = None
    context_id: str | None = None
    # Gateway mode: identifies the interview whose LLM config/secret/context
    # should be torn down best-effort on stop.
    interview_id: str | None = None


class ConcurrencyResponse(BaseModel):
    active_sessions: int


class TranscriptTurn(BaseModel):
    role: Literal["interviewer", "candidate"]
    text: str
    timestamp: float | None = None


class FinalizeTranscriptRequest(BaseModel):
    session_id: str
    turns: list[TranscriptTurn]
    # Gateway mode: when this resolves to a live interview, the saved record is
    # enriched with the vendor profile, scorecard, and Scout findings.
    interview_id: str | None = None


class CategoryScoreModel(BaseModel):
    id: str
    name: str
    weight: float
    score: float | None
    evidence: list[str]


class ScorecardModel(BaseModel):
    categories: list[CategoryScoreModel]
    overall: float | None


class ScoutFindingModel(BaseModel):
    topic: str
    summary: str
    source_url: str | None


class FollowupRecommendationModel(BaseModel):
    kind: Literal["advance", "clarify"]
    reason: str
    focus_categories: list[str]


class FollowupProposalModel(BaseModel):
    recommendation: FollowupRecommendationModel
    title: str
    agenda: list[str]
    duration_minutes: int
    email_draft: str


class FinalizeTranscriptResponse(BaseModel):
    summary: str
    # False when summary generation failed but the transcript was still saved.
    summary_ok: bool = True
    # Present only in gateway mode (interview_id resolved to a live interview);
    # the final values so the UI needs no extra state poll.
    scorecard: ScorecardModel | None = None
    insights: list[ScoutFindingModel] | None = None
    # Coordinator follow-up proposal; None when nothing is recommended, the
    # interview_id didn't resolve to a live interview, or the coordinator
    # failed (soft-fail).
    followup: FollowupProposalModel | None = None


class CreateInterviewResponse(BaseModel):
    interview_id: str


class InterviewStateResponse(BaseModel):
    status: Literal["created", "active", "finished"]
    # Topic of the current questionnaire node; None when the interview has
    # reached END (or the node id is unknown).
    current_topic: str | None
    insights: list[ScoutFindingModel]
    updated_at: str  # ISO-8601 UTC timestamp of this snapshot
