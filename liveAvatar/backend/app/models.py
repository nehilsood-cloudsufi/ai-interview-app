from typing import Any, Literal

from pydantic import BaseModel, Field


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
    role: Literal["interviewer", "candidate", "system"]
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


class CreateInterviewRequest(BaseModel):
    # Optional: missing/null resolves to settings.default_domain. An unknown
    # domain is rejected with a 400 by the router.
    domain: str | None = None


class CreateInterviewResponse(BaseModel):
    interview_id: str


class DomainInfo(BaseModel):
    id: str
    title: str


class DomainsResponse(BaseModel):
    domains: list[DomainInfo]


class VendorProfileModel(BaseModel):
    company_name: str
    website: str | None
    contact_name: str
    contact_role: str | None


class InterviewStateResponse(BaseModel):
    status: Literal["created", "active", "finished"]
    domain: str
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


class UpdateProfileRequest(BaseModel):
    # None = "not provided" (leave alone); a provided string (even empty
    # after strip) IS applied and locks the field against the LLM.
    company_name: str | None = None
    website: str | None = None
    contact_name: str | None = None
    contact_role: str | None = None


class UpdateProfileResponse(BaseModel):
    vendor_profile: VendorProfileModel
    # Sorted for determinism - the full set of profile fields ever manually
    # edited, which the Host's profile_updates merge will never overwrite.
    manually_edited_fields: list[str]


class ScoutRequest(BaseModel):
    """Request for the on-demand Data Scout Agent (POST /api/scout) - distinct
    from the automatic post-interview scout wired into app.services.pipeline.
    transcript is entered manually in the UI for now, until the interview
    module pipes it in via pub/sub later."""

    company_name: str = Field(description="The company or organization name to research.")
    company_website: str | None = Field(
        default=None, description="Optional company website URL; fetched (plus same-site subpages) during gathering."
    )
    representative_name: str | None = Field(
        default=None,
        description="Optional name of the interviewed company representative. Accepted for future use; not yet referenced in gathering or synthesis.",
    )
    representative_role: str | None = Field(
        default=None,
        description="Optional role/title of the interviewed company representative. Accepted for future use; not yet referenced in gathering or synthesis.",
    )
    transcript: str | None = Field(
        default=None,
        description="Optional interview transcript text. When provided, triggers Pass B: factual-claim extraction and targeted research on those claims.",
    )


class ScoutResponse(BaseModel):
    """Response for the on-demand Data Scout Agent (POST /api/scout) - distinct
    from ScoutFindingModel/insights above, which come from the automatic
    post-interview scout.

    internet_findings and interview_claims are deliberately separate, peer
    fields - not one field with both blended together - so the frontend can
    render them side by side without implying one verifies the other. Scout
    only gathers and presents; the Evaluator does all comparison."""

    scout_id: str = Field(description="Unique identifier for this scout report; usable with GET /api/scout/{scout_id} to retrieve it again.")
    internet_findings: str = Field(description="The synthesized findings report in GitHub-flavored Markdown, or an empty string if synthesis failed (see findings_ok).")
    interview_claims: list[str] = Field(default_factory=list, description="Factual claims extracted from the transcript, if one was provided; empty otherwise.")
    sources: dict[str, Any] = Field(
        default_factory=dict, description="Raw structured data gathered from public web sources, keyed by source type; only keys with actual data are present."
    )
    findings_ok: bool = Field(default=True, description="False when internet_findings synthesis failed but gathered sources were still saved.")
