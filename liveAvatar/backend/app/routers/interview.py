"""Interview creation, live-state, and text-chat endpoints for the
interviewer-side UI.

POST creates a fresh in-memory interview with an empty vendor profile - the
intake form is gone, so the profile is captured conversationally by the Host
agent's `intro`/`confirm_profile` onboarding nodes instead. GET is a
read-only snapshot: status, current topic, Scout insights collected so far,
vendor profile, and the post-interview pipeline's progress
(`pipeline_status`/`scorecard`/`recommendation`) once finalize hands the
interview off to `app.services.pipeline` - scoring is still a single holistic
pass that never runs mid-interview, but the UI polls this endpoint (rather
than the finalize response) to learn when it's ready. POST .../chat is the
low-bandwidth text fallback: it drives the same Host agent turn as the
avatar's /llm/{id}/v1/chat/completions gateway, but same-origin and
unauthenticated (no gateway_token in the browser) since it never leaves our
own backend. Same-origin UI endpoints like the rest of /api - no auth.
"""

import dataclasses
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.models import ChatRequest, ChatResponse, CreateInterviewResponse, InterviewStateResponse
from app.services import host_agent, interview_state
from app.services.interview_config import get_questionnaire, get_rubric
from app.services.interview_state import VendorProfile

router = APIRouter()


@router.post("/api/interview", response_model=CreateInterviewResponse)
async def create_interview():
    state = interview_state.create(VendorProfile())
    return {"interview_id": state.interview_id}


@router.get("/api/interview/{interview_id}/state", response_model=InterviewStateResponse)
async def get_interview_state(interview_id: str):
    state = interview_state.get(interview_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown interview")

    node = get_questionnaire().get(state.current_node_id)
    profile = state.vendor_profile
    return {
        "status": state.status,
        "current_topic": node.topic if node else None,
        "insights": [dataclasses.asdict(finding) for finding in state.scout_findings],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_status": state.pipeline_status,
        "scorecard": dataclasses.asdict(state.scorecard) if state.scorecard is not None else None,
        "recommendation": dataclasses.asdict(state.recommendation) if state.recommendation is not None else None,
        "vendor_profile": {
            # Deliberately excludes doc_text - it can be huge (matches the
            # finalize route's enrichment).
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

    result = await host_agent.handle_turn(state, text, get_questionnaire(), get_rubric())

    return {"reply": result.reply, "done": state.current_node_id == host_agent.END_NODE_ID}
