"""Transcript finalize + read-back endpoints.

Finalize is what the frontend calls once a session ends (whether the vendor
stopped it or HeyGen ended it on its own): it takes the captured turns,
generates a Markdown summary, and persists the record via
`app.services.transcript_store` (GCS when GCS_BUCKET is set, local JSON
otherwise). When the finalize request carries a live `interview_id`, it also
hands the interview to the background post-interview pipeline
(`app.services.pipeline`: Scout -> Evaluator -> Coordinator) - the scorecard,
insights, and recommendation are NOT in the finalize response; the frontend
polls GET /api/interview/{id}/state for those as the pipeline runs. The
summary is generated defensively so a summary failure never loses the
transcript, but a persistence failure does fail the request. The GET route
reads a saved record back for the transcript-download / review UI.
Same-origin UI endpoints - no auth.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.models import FinalizeTranscriptRequest, FinalizeTranscriptResponse
from app.services import interview_state, pipeline, summary_service, transcript_store

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/transcript/finalize", response_model=FinalizeTranscriptResponse)
async def finalize_transcript(body: FinalizeTranscriptRequest):
    """Persist a finished interview transcript and kick off scoring.

    The request body (`FinalizeTranscriptRequest`) carries the `session_id`
    to store the record under, the list of `turns`, and optionally an
    `interview_id`. When `interview_id` resolves to an interview still live
    in memory, the saved record is enriched with the vendor profile and
    domain, and the interview is handed to the background pipeline. Responds
    with a `FinalizeTranscriptResponse`: `summary` (Markdown, or "" if
    generation failed), `summary_ok` (False in that failure case, though the
    transcript is still saved), and `pipeline_status` ("interviewed" once the
    pipeline is enqueued, or None for the legacy no-interview_id path). The
    `scorecard`, `insights`, and `recommendation` fields are always null in
    this response - they arrive later via GET /api/interview/{id}/state
    polling, since the pipeline runs in the background after this returns.

    Fails with 400 if `turns` is empty, and 500 if the transcript cannot be
    persisted. The route is idempotent: a repeated finalize for an interview
    already handed to the pipeline short-circuits and returns the
    already-saved summary rather than re-enqueuing or re-saving.
    """
    if not body.turns:
        raise HTTPException(status_code=400, detail="Transcript has no turns to finalize")

    state = interview_state.get(body.interview_id) if body.interview_id else None

    # Idempotency guard: a double finalize call (e.g. a retried request) must
    # not re-enqueue the pipeline or re-save over a record the pipeline may
    # already be mutating. We claim the finalize immediately below (before
    # any awaited work) so two concurrent calls can't both pass this check;
    # a short-circuited retry best-effort loads the saved record so it isn't
    # handed back an empty summary.
    if state is not None and state.pipeline_status is not None:
        try:
            saved = await transcript_store.get(body.session_id)
        except Exception as e:
            logger.warning("Best-effort transcript lookup failed for session %s: %s", body.session_id, e)
            saved = None
        summary = saved.get("summary", "") if saved else ""
        summary_ok = saved.get("summary_ok", True) if saved else True
        return {
            "summary": summary,
            "summary_ok": summary_ok,
            "pipeline_status": state.pipeline_status,
            "scorecard": None,
            "insights": None,
            "recommendation": None,
        }

    if state is not None:
        # Claim the finalize before doing any awaited work, closing the race
        # where two concurrent finalizes both read pipeline_status as None.
        state.pipeline_status = "interviewed"

    try:
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
            payload["domain"] = state.domain
            payload["vendor_profile"] = {
                # Serializes the profile's three fields (company_name,
                # contact_name, contact_role); doc_text does not exist here.
                "company_name": profile.company_name,
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
    except HTTPException:
        # The claim must be released on failure so a retried finalize is
        # allowed to proceed rather than short-circuiting forever.
        if state is not None:
            state.pipeline_status = None
        raise

    if state is not None:
        state.status = "finished"
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
    """Read back a previously finalized transcript by its `session_id` path
    parameter. Responds with the stored record as saved by finalize (a JSON
    object with `session_id`, `created_at`, `turns`, `summary`,
    `summary_ok`, and, for gateway-mode interviews, `domain`,
    `vendor_profile`, and pipeline fields) - it is returned as-is, not
    reshaped through a response model. Fails with 404 if no record exists
    for that session id."""
    record = await transcript_store.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return record
