import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal

from app.models import TranscriptTurn

# evaluator_agent imports ScoutFinding from this module, and coordinator_agent
# imports Scorecard from evaluator_agent - so a real (runtime) import of
# either Scorecard or FollowupRecommendation here would be circular. They are
# only needed for type-checking, so TYPE_CHECKING + string annotations keep
# the fields typed without ever importing the agent modules at runtime.
if TYPE_CHECKING:
    from app.services.coordinator_agent import FollowupRecommendation
    from app.services.evaluator_agent import Scorecard

# Post-interview pipeline (Scout -> Evaluator -> Coordinator) progress, owned
# and mutated exclusively by app.services.pipeline.
PipelineStatus = Literal["interviewed", "scouting", "evaluating", "ready", "failed"]


@dataclass
class VendorProfile:
    # Filled in by conversation (Host agent), not at interview creation -
    # interview_state.create(VendorProfile()) must work with no args.
    company_name: str = ""
    website: str | None = None
    contact_name: str = ""
    contact_role: str | None = None


@dataclass
class ScoutFinding:
    topic: str
    summary: str
    source_url: str | None


@dataclass
class InterviewState:
    interview_id: str
    gateway_token: str
    vendor_profile: VendorProfile
    # The domain-specific questionnaire this interview follows (e.g.
    # "ai_ml"); resolves `get_questionnaire(domain)`/`get_start_node_id`.
    # The empty default only exists so tests can construct states directly
    # without going through create().
    domain: str = ""
    # Avatar tier, chosen by URL path on the frontend: "dev" = free sandbox
    # avatar (~1-min sessions), "prod" = settings.prod_avatar_id with
    # is_sandbox=false (passcode-gated, credit-burning). NOT the avatar/chat
    # "mode" concept used by host_agent.
    tier: Literal["dev", "prod"] = "dev"
    # Prod tier only: HeyGen max_session_duration for this interview's
    # sessions, derived from the start screen's duration pick at creation.
    # None on the dev tier (the sandbox ~1-min cap applies regardless).
    max_session_seconds: int | None = None
    # Stamped by the Host on the first avatar-mode turn of a clocked
    # (max_session_seconds-bearing) interview; the wrap-up thresholds in
    # host_agent count down from here. None until then / on dev tier.
    first_turn_at: datetime | None = None
    heygen_session_id: str | None = None
    llm_config_id: str | None = None
    secret_id: str | None = None
    context_id: str | None = None
    # Set by create() from the questionnaire's first node; the empty default
    # only exists so tests can construct states directly with explicit ids.
    current_node_id: str = ""
    followup_count: int = 0
    turns: list[TranscriptTurn] = field(default_factory=list)
    scout_findings: list[ScoutFinding] = field(default_factory=list)
    # Profile fields the vendor manually corrected via PATCH
    # /api/interview/{id}/profile - permanently locked against the Host's
    # LLM-reported profile_updates (manual wins; re-editing manually is
    # always allowed).
    manually_edited_fields: set[str] = field(default_factory=set)
    status: Literal["created", "active", "finished"] = "created"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Set by app.services.pipeline once finalize hands the state off to it;
    # None until then.
    pipeline_status: PipelineStatus | None = None
    scorecard: "Scorecard | None" = None
    recommendation: "FollowupRecommendation | None" = None


_interviews: dict[str, InterviewState] = {}


def create(
    profile: VendorProfile,
    domain: str,
    tier: Literal["dev", "prod"] = "dev",
    max_session_seconds: int | None = None,
) -> InterviewState:
    from app.services.interview_config import get_start_node_id

    prune_older_than()
    state = InterviewState(
        interview_id=uuid.uuid4().hex,
        gateway_token=secrets.token_urlsafe(32),
        vendor_profile=profile,
        domain=domain,
        tier=tier,
        max_session_seconds=max_session_seconds,
        current_node_id=get_start_node_id(domain),
    )
    _interviews[state.interview_id] = state
    return state


def get(interview_id: str) -> InterviewState | None:
    return _interviews.get(interview_id)


def get_by_token(token: str) -> InterviewState | None:
    for state in _interviews.values():
        if secrets.compare_digest(state.gateway_token, token):
            return state
    return None


def remove(interview_id: str) -> None:
    _interviews.pop(interview_id, None)


def prune_older_than(hours: int = 6) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    stale_ids = [interview_id for interview_id, state in _interviews.items() if state.created_at < cutoff]
    for interview_id in stale_ids:
        del _interviews[interview_id]
    return len(stale_ids)
