import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from app.models import TranscriptTurn


@dataclass
class VendorProfile:
    company_name: str
    website: str | None
    contact_name: str
    contact_role: str | None
    doc_text: str = ""


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
    status: Literal["created", "active", "finished"] = "created"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_interviews: dict[str, InterviewState] = {}


def create(profile: VendorProfile) -> InterviewState:
    from app.services.interview_config import get_start_node_id

    prune_older_than()
    state = InterviewState(
        interview_id=uuid.uuid4().hex,
        gateway_token=secrets.token_urlsafe(32),
        vendor_profile=profile,
        current_node_id=get_start_node_id(),
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
