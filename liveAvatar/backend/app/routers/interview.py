"""Interview creation + live-state endpoints for the interviewer-side UI.

POST creates a fresh in-memory interview with an empty vendor profile - the
intake form is gone, so the profile is captured conversationally by the Host
agent's `intro`/`confirm_profile` onboarding nodes instead. GET is a
read-only snapshot: status, current topic, and the Scout insights collected
so far. No scorecard here - scoring is a single holistic pass at finalize
time (deliberately never mid-interview), so the final scorecard arrives in
the finalize response instead. Same-origin UI endpoints like the rest of
/api - no auth.
"""

import dataclasses
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.models import CreateInterviewResponse, InterviewStateResponse
from app.services import interview_state
from app.services.interview_config import get_questionnaire
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
    return {
        "status": state.status,
        "current_topic": node.topic if node else None,
        "insights": [dataclasses.asdict(finding) for finding in state.scout_findings],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
