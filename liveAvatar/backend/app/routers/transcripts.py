import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.models import FinalizeTranscriptRequest, FinalizeTranscriptResponse
from app.services import interview_state, pipeline, summary_service, transcript_store

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/transcript/finalize", response_model=FinalizeTranscriptResponse)
async def finalize_transcript(body: FinalizeTranscriptRequest):
    if not body.turns:
        raise HTTPException(status_code=400, detail="Transcript has no turns to finalize")

    state = interview_state.get(body.interview_id) if body.interview_id else None

    # Idempotency guard: a double finalize call (e.g. a retried request) must
    # not re-enqueue the pipeline or re-save over a record the pipeline may
    # already be mutating. The first finalize's record already holds the
    # summary, so we just report the current status back.
    if state is not None and state.pipeline_status is not None:
        return {
            "summary": "",
            "summary_ok": True,
            "pipeline_status": state.pipeline_status,
            "scorecard": None,
            "insights": None,
            "recommendation": None,
        }

    # Generate the summary, but never let a summary failure lose the transcript.
    summary = ""
    summary_ok = True
    try:
        summary = await summary_service.generate_summary(body.turns)
    except Exception as e:
        summary_ok = False
        logger.warning("Summary generation failed for session %s: %s", body.session_id, e)

    payload = {
        "session_id": body.session_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "turns": [turn.model_dump() for turn in body.turns],
        "summary": summary,
        "summary_ok": summary_ok,
    }

    # Gateway mode: when the interview is still live in memory, hand it off
    # to the background pipeline (Scout -> Evaluator -> Coordinator) - the
    # scorecard/insights/recommendation arrive later via GET .../state
    # polling, not in this response. Legacy finalize (no/unknown
    # interview_id) keeps the exact shape above, with no pipeline at all.
    pipeline_status = None
    if state is not None:
        profile = state.vendor_profile
        payload["vendor_profile"] = {
            # Deliberately excludes doc_text - it can be huge.
            "company_name": profile.company_name,
            "website": profile.website,
            "contact_name": profile.contact_name,
            "contact_role": profile.contact_role,
        }
        pipeline_status = "interviewed"
        payload["pipeline_status"] = pipeline_status

    try:
        await transcript_store.save(body.session_id, payload)
    except Exception as e:
        logger.error("Failed to persist transcript %s: %s", body.session_id, e)
        raise HTTPException(status_code=500, detail="Failed to save transcript")

    if state is not None:
        state.status = "finished"
        state.pipeline_status = "interviewed"
        pipeline.enqueue(state, body.session_id, payload)

    return {
        "summary": summary,
        "summary_ok": summary_ok,
        "pipeline_status": pipeline_status,
        "scorecard": None,
        "insights": None,
        "recommendation": None,
    }


@router.get("/api/transcript/{session_id}")
async def get_transcript(session_id: str):
    record = await transcript_store.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return record
