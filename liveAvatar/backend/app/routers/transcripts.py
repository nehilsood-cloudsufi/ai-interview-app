import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.models import FinalizeTranscriptRequest, FinalizeTranscriptResponse
from app.services import summary_service, transcript_store

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/transcript/finalize", response_model=FinalizeTranscriptResponse)
async def finalize_transcript(body: FinalizeTranscriptRequest):
    if not body.turns:
        raise HTTPException(status_code=400, detail="Transcript has no turns to finalize")

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

    try:
        await transcript_store.save(body.session_id, payload)
    except Exception as e:
        logger.error("Failed to persist transcript %s: %s", body.session_id, e)
        raise HTTPException(status_code=500, detail="Failed to save transcript")

    return {"summary": summary, "summary_ok": summary_ok}


@router.get("/api/transcript/{session_id}")
async def get_transcript(session_id: str):
    record = await transcript_store.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return record
