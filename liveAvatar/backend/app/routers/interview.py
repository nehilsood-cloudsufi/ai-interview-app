"""Interview creation, live-state, and text-chat endpoints for the
interviewer-side UI.

POST creates a fresh in-memory interview with an empty vendor profile - the
intake form is gone, so the profile is captured conversationally by the Host
agent's `intro`/`confirm_profile` onboarding nodes instead. It also picks the
interview's domain-specific questionnaire (`domain` in the optional request
body, defaulting to `settings.default_domain`; an admin assigns this in
production). GET is a read-only snapshot: status, domain, current topic,
Scout insights collected so far, vendor profile, and the post-interview
pipeline's progress (`pipeline_status`/`scorecard`/`recommendation`) once
finalize hands the interview off to `app.services.pipeline` - scoring is
still a single holistic pass that never runs mid-interview, but the UI polls
this endpoint (rather than the finalize response) to learn when it's ready.
POST .../chat is the low-bandwidth text fallback: it drives the same Host
agent turn as the avatar's /llm/{id}/v1/chat/completions gateway, but
same-origin and unauthenticated (no gateway_token in the browser) since it
never leaves our own backend. PATCH .../profile lets the vendor manually
correct the Host-captured profile at any point in the interview; corrected
fields are permanently locked against the Host's LLM-reported
profile_updates and a role="system" note turn is appended so the transcript
(and the Evaluator) sees that a correction happened. Same-origin UI
endpoints like the rest of /api - no auth.
"""

import dataclasses
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.models import (
    ChatRequest,
    ChatResponse,
    CreateInterviewRequest,
    CreateInterviewResponse,
    DomainsResponse,
    InterviewStateResponse,
    TranscriptTurn,
    UpdateProfileRequest,
    UpdateProfileResponse,
)
from app.services import host_agent, interview_state
from app.services.interview_config import get_questionnaire, get_rubric, list_domains
from app.services.interview_state import VendorProfile

router = APIRouter()

# Maps UpdateProfileRequest/VendorProfile field names to their display label
# in the system-note turn, in canonical (request-shape) order. Clearing a
# required-str field (_REQUIRED_STR_FIELDS) sets "", clearing an optional
# field sets None.
_PROFILE_FIELD_LABELS = {
    "company_name": "Company name",
    "website": "Website",
    "contact_name": "Contact name",
    "contact_role": "Contact role",
}
_REQUIRED_STR_FIELDS = {"company_name", "contact_name"}


@router.get("/api/domains", response_model=DomainsResponse)
async def get_domains():
    return {"domains": [{"id": domain_id, "title": title} for domain_id, title in list_domains()]}


@router.post("/api/interview", response_model=CreateInterviewResponse)
async def create_interview(body: CreateInterviewRequest | None = None):
    domain = (body.domain if body else None) or settings.default_domain
    try:
        get_questionnaire(domain)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown domain: {domain!r}")

    state = interview_state.create(VendorProfile(), domain)
    return {"interview_id": state.interview_id}


@router.get("/api/interview/{interview_id}/state", response_model=InterviewStateResponse)
async def get_interview_state(interview_id: str):
    state = interview_state.get(interview_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown interview")

    node = get_questionnaire(state.domain).get(state.current_node_id)
    profile = state.vendor_profile
    return {
        "status": state.status,
        "domain": state.domain,
        "current_topic": node.topic if node else None,
        "insights": [dataclasses.asdict(finding) for finding in state.scout_findings],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_status": state.pipeline_status,
        "scorecard": dataclasses.asdict(state.scorecard) if state.scorecard is not None else None,
        "recommendation": dataclasses.asdict(state.recommendation) if state.recommendation is not None else None,
        "vendor_profile": {
            # Serializes the profile's four fields (company_name, website,
            # contact_name, contact_role); matches the finalize route's
            # enrichment.
            "company_name": profile.company_name,
            "website": profile.website,
            "contact_name": profile.contact_name,
            "contact_role": profile.contact_role,
        },
    }


@router.post("/api/interview/{interview_id}/chat", response_model=ChatResponse)
async def chat(interview_id: str, body: ChatRequest):
    """Same-origin text-chat fallback for low-bandwidth users: drives the
    identical Host agent conversation as the avatar, without exposing the
    per-interview gateway_token (unlike /llm/{id}/v1, which requires it)."""
    state = interview_state.get(interview_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown interview")

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    if state.status == "created":
        state.status = "active"

    result = await host_agent.handle_turn(state, text, get_questionnaire(state.domain), get_rubric(), mode="chat")

    return {"reply": result.reply, "done": state.current_node_id == host_agent.END_NODE_ID}


def _fmt_profile_value(value: str | None) -> str:
    return f'"{value}"' if value else "(not set)"


@router.patch("/api/interview/{interview_id}/profile", response_model=UpdateProfileResponse)
async def update_profile(interview_id: str, body: UpdateProfileRequest):
    """Vendor-initiated manual correction of the Host-captured profile,
    available at any point in the interview. Every provided field overwrites
    `state.vendor_profile` and is permanently added to
    `state.manually_edited_fields`, locking it against the Host's
    LLM-reported profile_updates on future turns (re-editing manually is
    always allowed). A role="system" note turn documenting the actual value
    changes is appended to `state.turns` - but only when something actually
    changed, so a no-op PATCH (re-submitting the same value) doesn't spam the
    transcript, even though the field still locks."""
    state = interview_state.get(interview_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Interview not found")

    provided_fields = [name for name in _PROFILE_FIELD_LABELS if getattr(body, name) is not None]
    if not provided_fields:
        raise HTTPException(status_code=400, detail="At least one profile field must be provided")

    profile = state.vendor_profile
    changes: list[tuple[str, str | None, str | None]] = []
    for field_name in provided_fields:
        stripped = getattr(body, field_name).strip()
        new_value = stripped if stripped else ("" if field_name in _REQUIRED_STR_FIELDS else None)
        old_value = getattr(profile, field_name)
        if new_value != old_value:
            changes.append((_PROFILE_FIELD_LABELS[field_name], old_value, new_value))
        setattr(profile, field_name, new_value)
        state.manually_edited_fields.add(field_name)

    if changes:
        note = "; ".join(
            f"{label}: {_fmt_profile_value(old)} → {_fmt_profile_value(new)}" for label, old, new in changes
        )
        state.turns.append(
            TranscriptTurn(role="system", text=f"[Vendor manually corrected their profile: {note}]")
        )

    return {
        "vendor_profile": {
            "company_name": profile.company_name,
            "website": profile.website,
            "contact_name": profile.contact_name,
            "contact_role": profile.contact_role,
        },
        "manually_edited_fields": sorted(state.manually_edited_fields),
    }
