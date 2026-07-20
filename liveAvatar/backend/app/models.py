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


class FinalizeTranscriptResponse(BaseModel):
    summary: str
    # False when summary generation failed but the transcript was still saved.
    summary_ok: bool = True
    # Gateway mode: "interviewed" once finalize hands the interview to the
    # background pipeline, progressing to "ready"/"failed" as it runs -
    # polled via GET /api/interview/{id}/state. None for the legacy
    # (no/unknown interview_id) path, which has no pipeline at all.
    pipeline_status: str | None = None
    # Scorecard/insights/recommendation now arrive via polling, not in this
    # response - the pipeline runs in the background after finalize returns,
    # so these are always None in gateway mode too.
    scorecard: ScorecardModel | None = None
    insights: list[ScoutFindingModel] | None = None
    recommendation: FollowupRecommendationModel | None = None


class CreateInterviewResponse(BaseModel):
    interview_id: str


class VendorProfileModel(BaseModel):
    company_name: str
    website: str | None
    contact_name: str
    contact_role: str | None


class InterviewStateResponse(BaseModel):
    status: Literal["created", "active", "finished"]
    # Topic of the current questionnaire node; None when the interview has
    # reached END (or the node id is unknown).
    current_topic: str | None
    insights: list[ScoutFindingModel]
    updated_at: str  # ISO-8601 UTC timestamp of this snapshot
    # Post-interview pipeline progress (None until finalize hands the
    # interview to app.services.pipeline); the UI polls this endpoint for it.
    pipeline_status: str | None = None
    scorecard: ScorecardModel | None = None
    recommendation: FollowupRecommendationModel | None = None
    vendor_profile: VendorProfileModel


class ChatRequest(BaseModel):
    text: str


class ChatResponse(BaseModel):
    reply: str
    # True once the questionnaire has reached host_agent.END_NODE_ID.
    done: bool
