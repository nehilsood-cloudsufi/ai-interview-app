"""The in-memory registry of live interviews.

One `InterviewState` per interview holds everything the live interview and the
post-interview pipeline need: the vendor profile, the running turn history, the
current position in the domain questionnaire, follow-up accounting, Scout
findings, the set of manually-corrected profile fields, and the eventual
pipeline status / scorecard / recommendation. States live in the module-level
`_interviews` dict for the life of the process (no database), so a backend
restart drops all in-flight interviews - acceptable for a POC. `create` prunes
anything older than 6 hours on every call so abandoned interviews don't
accumulate; there is no explicit remove - the pipeline runs to completion and
the record is persisted to `transcript_store`, then the state is left to be
pruned by age."""

import asyncio
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
    """The vendor's identity details, entered on the start screen's intake
    form at interview creation (name/company required there, role optional)
    and later correctable by the vendor via PATCH /api/interview/{id}/profile
    or a spoken correction (the Host reports `profile_updates` on every turn).
    All fields default to empty so `create` can construct one with no
    arguments when an API caller provides nothing."""

    company_name: str = ""
    contact_name: str = ""
    contact_role: str | None = None


@dataclass
class ScoutFinding:
    """One item of post-interview company research produced by the Scout agent:
    a topic, a short summary, and the grounding source URL (None when the
    finding is not tied to a single citable page)."""

    topic: str
    summary: str
    source_url: str | None


@dataclass
class InterviewState:
    """Everything tracked for a single interview from creation through scoring.

    Created by `create` with the identity/routing fields set (`interview_id`,
    `gateway_token`, `domain`, `tier`, the questionnaire's start node) and
    mutated in place thereafter: `host_agent` appends `turns`, advances
    `current_node_id`, and updates `followup_count`/`vendor_profile`;
    `pipeline` fills `scout_findings`, `scorecard`, `recommendation`, and drives
    `pipeline_status`. The HeyGen resource ids (`heygen_session_id`,
    `llm_config_id`, `secret_id`, `context_id`) are recorded for later teardown.
    Most fields carry defaults so tests can construct a state directly without
    going through `create`. See the inline comments for the per-field nuances."""

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
    # Ordered node ids THIS interview will ask (a subset of the domain's
    # questionnaire when a prod-tier duration only fits the top-K questions;
    # see interview_config.build_question_plan). host_agent advances along
    # this plan instead of the nodes' own `next` pointers; an empty list
    # (states built directly in tests) falls back to `next`.
    question_plan: list[str] = field(default_factory=list)
    # Plain-language bullet summary of the intake material the vendor
    # submitted up front (the "about you" text and any uploaded documents),
    # produced by a Gemini flash call at creation/upload time. Given to the
    # Host (to phrase questions naturally) and the Evaluator (as background);
    # empty when the vendor submitted nothing.
    vendor_context: str = ""
    followup_count: int = 0
    # Consecutive host_agent turns that soft-failed (HTTP error or unparsable
    # JSON) in a row, reset to 0 on the next successful turn. Never gates or
    # mutates script state - it only picks which fallback reply/log level a
    # failing turn gets (see host_agent._handle_turn / _stream_turn).
    consecutive_soft_fails: int = 0
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
    # Serializes host_agent.handle_turn/stream_turn per interview. HeyGen's
    # VAD can fire several gateway calls near-simultaneously when it splits
    # one flowing answer into fragments (seen live 2026-07-22: two turns
    # processed concurrently on the same node); without this, both turns read
    # the same current_node_id and the script double-advances.
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
    # Monotonic counter of gateway utterance requests, bumped by llm_gateway
    # per real user turn. A turn whose recorded seq no longer equals this head
    # has been superseded by a newer speech fragment and must not run (its
    # reply will never be spoken). Our own bookkeeping - request.is_disconnected
    # proved unreliable through the tunnel/uvicorn stack (2026-07-22).
    request_seq: int = 0


_interviews: dict[str, InterviewState] = {}


def create(
    profile: VendorProfile,
    domain: str,
    tier: Literal["dev", "prod"] = "dev",
    max_session_seconds: int | None = None,
    question_plan: list[str] | None = None,
) -> InterviewState:
    """Register a fresh interview and return its `InterviewState`.

    Mints the `interview_id` and the `gateway_token` HeyGen will present on
    its Custom LLM callbacks, seeds `current_node_id` from the question plan's
    first node (falling back to the domain questionnaire's start node when no
    plan is given), and stores the state in `_interviews`. Prunes stale
    interviews first (see `prune_older_than`) so the registry stays bounded.
    The `interview_config` import is deferred to call time to avoid an import
    cycle."""
    from app.services.interview_config import get_start_node_id

    prune_older_than()
    state = InterviewState(
        interview_id=uuid.uuid4().hex,
        gateway_token=secrets.token_urlsafe(32),
        vendor_profile=profile,
        domain=domain,
        tier=tier,
        max_session_seconds=max_session_seconds,
        question_plan=question_plan or [],
        current_node_id=question_plan[0] if question_plan else get_start_node_id(domain),
    )
    _interviews[state.interview_id] = state
    return state


def get(interview_id: str) -> InterviewState | None:
    """Look up a live interview by id, or None if it was never created or has
    already been pruned."""
    return _interviews.get(interview_id)


def prune_older_than(hours: int = 6) -> int:
    """Drop every interview created more than `hours` ago and return how many
    were removed. Called from `create` on each new interview so abandoned
    states (the frontend closed, the pipeline never ran) can't accumulate in
    memory for the life of the process."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    stale_ids = [interview_id for interview_id, state in _interviews.items() if state.created_at < cutoff]
    for interview_id in stale_ids:
        del _interviews[interview_id]
    return len(stale_ids)
