"""Pydantic request/response models shared across the routers.

Each model is the typed contract for one or more endpoints (FastAPI validates
request bodies against them and shapes responses with them, which is what the
`/docs` OpenAPI schema is generated from). Names ending in `Request` are
request bodies, those ending in `Response` are top-level response shapes, and
the rest (`TranscriptTurn`, `CategoryScoreModel`, `ScorecardModel`,
`ScoutReportModel`, `FollowupRecommendationModel`, `DomainInfo`,
`VendorProfileModel`) are nested pieces reused inside the request/response
models. Inline comments on individual fields document the non-obvious ones.
"""

from typing import Literal

from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    """Request body for POST /api/session. `interview_id` names the interview
    to open a LiveAvatar stream for; `avatar_id` is accepted but currently
    ignored (the avatar is chosen server-side from the interview's tier)."""

    avatar_id: str | None = None
    # Identifies the interview whose gateway session should be created; the
    # session gets a per-interview Custom LLM pointing back at our
    # /llm/{id}/v1 gateway.
    interview_id: str | None = None


class StopSessionRequest(BaseModel):
    """Request body for POST /api/session/stop. `session_token` identifies the
    live stream to stop; `context_id` and `interview_id` let the endpoint also
    tear down the per-interview LiveAvatar resources provisioned at creation."""

    session_token: str | None = None
    context_id: str | None = None
    # Gateway mode: identifies the interview whose LLM config/secret/context
    # should be torn down best-effort on stop.
    interview_id: str | None = None


class ConcurrencyResponse(BaseModel):
    """Response for GET /api/concurrency: the current active-session count."""

    active_sessions: int


class TranscriptTurn(BaseModel):
    """One line of an interview transcript, used both in the finalize request
    body and in the persisted record. `role` is the speaker: "interviewer"
    (the Host/avatar), "candidate" (the vendor), or "system" (an injected note,
    e.g. a manual profile correction). `timestamp` is an optional epoch seconds
    value captured client-side."""

    role: Literal["interviewer", "candidate", "system"]
    text: str
    timestamp: float | None = None


class FinalizeTranscriptRequest(BaseModel):
    """Request body for POST /api/transcript/finalize. `session_id` is the key
    the record is stored under, `turns` is the full captured transcript, and
    the optional `interview_id` links it to the live interview so the record
    is enriched and the post-interview pipeline is triggered."""

    session_id: str
    turns: list[TranscriptTurn]
    # Gateway mode: when this resolves to a live interview, the saved record is
    # enriched with the vendor profile, scorecard, and Scout findings.
    interview_id: str | None = None


class CategoryScoreModel(BaseModel):
    """One rubric category's result inside a `ScorecardModel`. `id`/`name`/
    `weight` describe the category; `value` is the categorical label the
    Evaluator chose (None if the category went unscored), `points` is that
    label resolved to its numeric points, and `evidence` holds the supporting
    quotes from the transcript."""

    id: str
    name: str
    weight: float
    value: str | None
    points: float | None
    evidence: list[str]


class ScorecardModel(BaseModel):
    """The Evaluator's scorecard, nested in the state and finalize responses.
    `categories` is the per-category breakdown, `overall` is the 0-100
    weighted score (None if nothing was scored), and `status` is the
    code-computed APPROVED/REJECTED verdict (None when there is no overall)."""

    categories: list[CategoryScoreModel]
    overall: float | None
    status: Literal["APPROVED", "REJECTED"] | None


class ScoutReportModel(BaseModel):
    """The Data Scout Agent's full post-interview company research report
    (nested as `insights` in the state and finalize responses) - the
    two-pass Tavily-backed agent (app.services.data_scout_agent) triggered
    automatically by app.services.pipeline once an interview finalizes.
    `internet_findings` is the synthesized Markdown report, `interview_claims`
    are the factual claims extracted from the transcript/intake material,
    `findings_ok` is False when synthesis failed but sources were still
    gathered, and `company_website` is whatever the agent derived (or None).
    The raw `sources` dict is deliberately NOT included here - it's far too
    large/granular for the UI."""

    internet_findings: str
    interview_claims: list[str]
    findings_ok: bool
    company_website: str | None


class FollowupRecommendationModel(BaseModel):
    """The Coordinator's next-step recommendation (nested in the state and
    finalize responses). `kind` is "advance" or "clarify", `reason` explains
    it in prose, and `focus_categories` names the rubric categories a
    clarification call should probe."""

    kind: Literal["advance", "clarify"]
    reason: str
    focus_categories: list[str]


class FinalizeTranscriptResponse(BaseModel):
    """Response for POST /api/transcript/finalize. Carries the generated
    `summary` and `summary_ok`, and `pipeline_status` for gateway-mode
    interviews. The `scorecard`/`insights`/`recommendation` fields exist for
    shape stability but are always None here - they are produced by the
    background pipeline and fetched via GET /api/interview/{id}/state."""

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
    insights: ScoutReportModel | None = None
    recommendation: FollowupRecommendationModel | None = None


class CreateInterviewRequest(BaseModel):
    """Request body for POST /api/interview (the whole body is optional). Picks
    the interview's `domain`, avatar `tier`, and for the prod tier the
    `passcode` and session `duration_minutes`. See the router for the exact
    defaults and validation errors each field can trigger."""

    # Optional: missing/null resolves to settings.default_domain. An unknown
    # domain is rejected with a 400 by the router.
    domain: str | None = None
    # Avatar tier ("dev" | "prod"); missing/null resolves to "dev". Anything
    # else is rejected with a 400 by the router. "prod" additionally requires
    # the correct passcode and a configured PROD_AVATAR_ID.
    tier: str | None = None
    # Shared demo passcode, only consulted for tier="prod" (DEMO_PASSCODE).
    passcode: str | None = None
    # Prod tier only: how long the session may run, picked on the start
    # screen. Missing/null defaults to 5 minutes; the router 400s outside
    # 1..(PROD_MAX_SESSION_SECONDS/60). Ignored on the dev tier (HeyGen's
    # ~1-min sandbox cap applies regardless).
    duration_minutes: int | None = None
    # Start-screen intake: the vendor's identity, entered up front instead of
    # being asked conversationally (the onboarding questionnaire nodes are
    # gone). The frontend requires name and company; the API keeps all three
    # optional for backward compatibility. Provided fields pre-fill the
    # vendor profile and lock against the Host's LLM-reported updates.
    contact_name: str | None = None
    contact_role: str | None = None
    company_name: str | None = None
    # Optional free-text "about you / your company" from the intake form,
    # summarized into state.vendor_context at creation.
    about_text: str | None = None


class CreateInterviewResponse(BaseModel):
    """Response for POST /api/interview: the id of the newly created
    interview, needed for every subsequent call (session, chat, state, etc.)."""

    interview_id: str


class UploadDocumentResponse(BaseModel):
    """Response for POST /api/interview/{id}/document: the stored context
    document's `filename`, its original `word_count`, and whether it was
    `truncated` to the intake word limit before summarization (oversize
    documents are trimmed, never rejected - the frontend shows a short
    notice when this flag is set)."""

    filename: str
    word_count: int
    truncated: bool


class DomainInfo(BaseModel):
    """One selectable interview domain (nested in `DomainsResponse`): the
    questionnaire's `id` and its human-readable `title`."""

    id: str
    title: str


class DomainsResponse(BaseModel):
    """Response for GET /api/domains: the list of available `(id, title)`
    domain pairs for a domain picker, plus which domain id the server treats
    as the default (settings.default_domain) so the picker can preselect it."""

    domains: list[DomainInfo]
    default: str


class VendorProfileModel(BaseModel):
    """The vendor's captured profile, nested in the state and profile
    responses. `company_name`/`contact_name` are strings (possibly empty
    before onboarding fills them), `contact_role` is optional."""

    company_name: str
    contact_name: str
    contact_role: str | None


class InterviewStateResponse(BaseModel):
    """Response for GET /api/interview/{id}/state: a full read-only snapshot of
    one interview - lifecycle `status`, `domain`, `current_topic`, Scout
    `insights`, the `updated_at` stamp, the current `vendor_profile`, and the
    post-interview pipeline fields (`pipeline_status`/`scorecard`/
    `recommendation`) that stay null until finalize starts the pipeline."""

    status: Literal["created", "active", "finished"]
    domain: str
    # Topic of the current questionnaire node; None when the interview has
    # reached END (or the node id is unknown).
    current_topic: str | None
    # True once the script has reached END - the closing has been (or is
    # being) spoken and no further questions remain. The avatar frontend
    # auto-stops the session shortly after this flips, so post-interview
    # utterances can't loop the canned closing forever.
    done: bool = False
    insights: ScoutReportModel | None
    updated_at: str  # ISO-8601 UTC timestamp of this snapshot
    # Post-interview pipeline progress (None until finalize hands the
    # interview to app.services.pipeline); the UI polls this endpoint for it.
    pipeline_status: str | None = None
    scorecard: ScorecardModel | None = None
    recommendation: FollowupRecommendationModel | None = None
    # True when the pipeline's Evaluator call raised (e.g. a too-short
    # transcript) - scorecard stays null in that case too, but this flag lets
    # the frontend tell the vendor scoring genuinely failed rather than
    # silently rendering nothing.
    evaluation_failed: bool = False
    vendor_profile: VendorProfileModel


class ChatRequest(BaseModel):
    """Request body for POST /api/interview/{id}/chat: `text` is the vendor's
    typed message for this turn of the text-chat fallback."""

    text: str


class ChatResponse(BaseModel):
    """Response for POST /api/interview/{id}/chat: the Host's `reply` and
    `done`, which flips True once the questionnaire has reached its END
    node."""

    reply: str
    # True once the questionnaire has reached host_agent.END_NODE_ID.
    done: bool


class UpdateProfileRequest(BaseModel):
    """Request body for PATCH /api/interview/{id}/profile: any subset of the
    profile fields to correct. Per the field comment, None means "not
    provided" (left alone), while any provided value is applied and locks that
    field against future LLM-reported updates."""

    # None = "not provided" (leave alone); a provided string (even empty
    # after strip) IS applied and locks the field against the LLM.
    company_name: str | None = None
    contact_name: str | None = None
    contact_role: str | None = None


class UpdateProfileResponse(BaseModel):
    """Response for PATCH /api/interview/{id}/profile: the `vendor_profile`
    after the correction and the full `manually_edited_fields` set (the fields
    now locked against LLM-reported updates)."""

    vendor_profile: VendorProfileModel
    # Sorted for determinism - the full set of profile fields ever manually
    # edited, which the Host's profile_updates merge will never overwrite.
    manually_edited_fields: list[str]


# --- DEV-ONLY: Data Scout Agent manual test harness (app.routers.scout_test) ---
# Not part of the real interview flow - lets a developer run
# app.services.data_scout_agent.run_scout directly with hand-entered inputs.


class ScoutTestRequest(BaseModel):
    """DEV-ONLY request body for POST /api/scout-test: every input
    app.services.data_scout_agent.run_scout accepts, entered manually instead
    of coming from a live interview. `documents_text` is a single pasted
    document body (wrapped into run_scout's `documents: list[str] | None` by
    the router), and `email` exercises Representative Identity Verification
    (app.services.representative_verification) - not yet reachable from the
    real intake form."""

    company_name: str = ""
    representative_name: str = ""
    email: str = ""
    extra_context: str = ""
    documents_text: str = ""
    transcript: str = ""


class RepresentativeVerificationBackgroundModel(BaseModel):
    """Mirrors representative_verification.RepresentativeVerification's
    `background` dict shape."""

    tenure: str
    projects: list[str]
    focus_areas: list[str]


class RepresentativeVerificationEvidenceModel(BaseModel):
    """One cited source (VERIFIED only): mirrors an item in
    representative_verification.RepresentativeVerification's `evidence`
    list - the URL plus a short "supports" phrase (from the LLM's per-item
    citation) stating what that specific source confirms, so a reviewer
    never has to open the link to know why it's cited."""

    url: str
    source_name: str
    supports: str


class RepresentativeVerificationModel(BaseModel):
    """Mirrors app.services.representative_verification.RepresentativeVerification
    field-for-field (DEV-ONLY response use - the production ScoutReportModel
    below deliberately omits this; its rendered markdown is already prepended
    into `internet_findings` for the real UI)."""

    verdict: Literal["VERIFIED", "NOT_FOUND", "NO_DATA"]
    email_valid: bool
    email_type: Literal["corporate", "personal", "invalid", "missing"]
    email_domain: str
    email_domain_plausible: bool
    role: str
    summary: str
    background: RepresentativeVerificationBackgroundModel
    evidence: list[RepresentativeVerificationEvidenceModel]


class ScoutTestResponse(BaseModel):
    """DEV-ONLY response for POST /api/scout-test: the full run_scout result.
    Unlike ScoutReportModel (the production `insights` shape), this includes
    the raw `sources` dict and the full `representative_verification`
    structure - the whole point of this harness is to inspect everything
    Scout gathered and decided, not just the rendered report."""

    internet_findings: str
    interview_claims: list[str]
    sources: dict
    findings_ok: bool
    company_website: str | None
    representative_verification: RepresentativeVerificationModel
