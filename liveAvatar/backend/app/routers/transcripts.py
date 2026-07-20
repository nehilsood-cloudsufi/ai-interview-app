import dataclasses
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.models import FinalizeTranscriptRequest, FinalizeTranscriptResponse
from app.services import (
    coordinator_agent,
    evaluator_agent,
    interview_state,
    summary_service,
    transcript_store,
)
from app.services.interview_config import get_rubric

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

    # Gateway mode: when the interview is still live in memory, enrich the
    # record with the vendor profile, final scorecard, and Scout findings.
    # Legacy finalize (no/unknown interview_id) keeps the exact shape above.
    scorecard_dict = None
    insights = None
    recommendation = None
    state = interview_state.get(body.interview_id) if body.interview_id else None
    if state is not None:
        profile = state.vendor_profile
        rubric = get_rubric()
        insights = [dataclasses.asdict(finding) for finding in state.scout_findings]
        payload["vendor_profile"] = {
            # Deliberately excludes doc_text - it can be huge.
            "company_name": profile.company_name,
            "website": profile.website,
            "contact_name": profile.contact_name,
            "contact_role": profile.contact_role,
        }
        payload["scout_findings"] = insights

        # Holistic scoring: one pro-model pass over the Host's authoritative
        # transcript (state.turns, not the frontend-captured body.turns), plus
        # any independent Scout research findings. Like the summary, a scoring
        # failure must never lose the transcript.
        scorecard = None
        try:
            scorecard = await evaluator_agent.score_interview(state.turns, rubric, state.scout_findings)
            scorecard_dict = dataclasses.asdict(scorecard)
        except Exception as e:
            logger.warning("Holistic scoring failed for session %s: %s", body.session_id, e)
        payload["scorecard"] = scorecard_dict

        # Coordinator: pure threshold rule over the final scorecard, skipped
        # entirely when scoring produced no usable overall. evaluate_followup
        # is deterministic (no LLM, no I/O) so it needs no soft-fail wrapper.
        if scorecard is not None and scorecard.overall is not None:
            rec = coordinator_agent.evaluate_followup(scorecard, rubric)
            if rec is not None:
                recommendation = dataclasses.asdict(rec)
        payload["recommendation"] = recommendation

        state.status = "finished"

    try:
        await transcript_store.save(body.session_id, payload)
    except Exception as e:
        logger.error("Failed to persist transcript %s: %s", body.session_id, e)
        raise HTTPException(status_code=500, detail="Failed to save transcript")

    return {
        "summary": summary,
        "summary_ok": summary_ok,
        "scorecard": scorecard_dict,
        "insights": insights,
        "recommendation": recommendation,
    }


@router.get("/api/transcript/{session_id}")
async def get_transcript(session_id: str):
    record = await transcript_store.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return record
