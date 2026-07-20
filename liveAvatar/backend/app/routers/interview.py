"""Live interview-state endpoint for the interviewer-side UI.

Read-only snapshot of an in-memory interview: status, current topic, and the
Scout insights collected so far. No scorecard here - scoring is a single
holistic pass at finalize time (deliberately never mid-interview), so the
final scorecard arrives in the finalize response instead. Same-origin UI
endpoint like the rest of /api - no auth.
"""

import dataclasses
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.models import InterviewStateResponse
from app.services import interview_state
from app.services.interview_config import get_questionnaire

router = APIRouter()


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
