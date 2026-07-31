"""Post-interview background pipeline: sequences Scout -> Evaluator ->
Coordinator after an interview finishes, owns `state.pipeline_status`, and
persists the final transcript record. This module is THE ONLY orchestrator -
agents never call each other, only this module composes them.
"""

import asyncio
import dataclasses
import logging

from app.config import settings
from app.services import coordinator_agent, data_scout_agent, evaluator_agent, scout_utils, transcript_store
from app.services.interview_config import get_rubric
from app.services.interview_state import InterviewState, ScoutReport
from app.services.transcript_render import render_transcript

logger = logging.getLogger(__name__)

# Hard wall-clock ceiling on one Scout run. run_scout's own budgets (45s
# gather + up to 3 x 180s synthesis attempts) can legitimately stack to ~10
# minutes worst case, and nothing above the pipeline enforces a deadline -
# under Cloud Run CPU throttling an instance scale-down mid-retry would lose
# the interview record outright. Generous enough for the normal case (gather
# + one synthesis attempt + one retry), tight enough to bound the tail.
_SCOUT_DEADLINE_SECONDS = 300.0

# Tasks scheduled by enqueue() are kept here so they aren't garbage-collected
# mid-flight (asyncio only holds a weak reference to a task via
# create_task); each task discards itself once done.
_tasks: set = set()


def enqueue(state: InterviewState, session_id: str, payload: dict) -> None:
    """Schedule the post-interview pipeline to run in the background.

    In-process seam for MVP-1. Prod: replace this function body with a GCP
    Pub/Sub publish and move run() into a push-subscriber endpoint - nothing
    else changes.
    """
    task = asyncio.create_task(run(state, session_id, payload))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def run(state: InterviewState, session_id: str, payload: dict) -> None:
    """Scout -> Evaluator -> Coordinator over one finished interview, then
    persist the enriched record. Never raises: any unexpected failure is
    logged and the pipeline status is set to "failed" instead."""
    try:
        rubric = get_rubric()

        state.pipeline_status = "scouting"
        scout_report = None
        profile = state.vendor_profile
        if not settings.scout_enabled:
            logger.info("Data Scout disabled (scout_enabled=False); skipping interview %s.", state.interview_id)
        elif not profile.company_name.strip():
            logger.info("No company name captured for interview %s; skipping Scout research.", state.interview_id)
        else:
            # Soft-fail boundary, mirroring score_interview below: a research
            # hiccup (search API glitch, fetch failure, synthesis error) must
            # never break the Evaluator/Coordinator steps that follow - the
            # old scout_agent guaranteed this by never raising; run_scout
            # does not, so the guarantee lives here now.
            try:
                scout_result = await asyncio.wait_for(
                    data_scout_agent.run_scout(
                        profile.company_name,
                        profile.contact_name,
                        state.vendor_context,
                        None,
                        render_transcript(state.turns),
                    ),
                    timeout=_SCOUT_DEADLINE_SECONDS,
                )
                scout_report = ScoutReport(
                    internet_findings=scout_result["internet_findings"],
                    interview_claims=scout_result["interview_claims"],
                    sources=scout_result["sources"],
                    findings_ok=scout_result["findings_ok"],
                    company_website=scout_result["company_website"],
                )
            except Exception as e:
                logger.warning(
                    "Data Scout research failed for session %s; continuing without a report: %s", session_id, e
                )
        state.scout_report = scout_report

        state.pipeline_status = "evaluating"
        scorecard = None
        try:
            scorecard = await evaluator_agent.score_interview(
                state.turns, rubric, scout_report, vendor_context=state.vendor_context
            )
        except Exception as e:
            logger.warning("Holistic scoring failed for session %s: %s", session_id, e)
            state.evaluation_failed = True
            payload["evaluation_failed"] = True

        rec = None
        if scorecard is not None and scorecard.overall is not None:
            rec = coordinator_agent.evaluate_followup(scorecard, rubric)

        state.scorecard = scorecard
        state.recommendation = rec

        # Persist with sources TRUNCATED (same caps the synthesis payload
        # gets): the raw dict can carry hundreds of KB of scraped third-party
        # page text per interview, which was ballooning every GCS record and
        # getting served verbatim by GET /api/transcript/{id}.
        payload["scout_report"] = (
            {**dataclasses.asdict(scout_report), "sources": scout_utils._truncate_sources(scout_report.sources)}
            if scout_report is not None
            else None
        )
        payload["scorecard"] = dataclasses.asdict(scorecard) if scorecard is not None else None
        payload["recommendation"] = dataclasses.asdict(rec) if rec is not None else None
        payload["pipeline_status"] = "ready"

        await transcript_store.save(session_id, payload)
        state.pipeline_status = "ready"
    except Exception:
        logger.error("Post-interview pipeline failed for session %s", session_id, exc_info=True)
        state.pipeline_status = "failed"
        try:
            payload["pipeline_status"] = "failed"
            await transcript_store.save(session_id, payload)
        except Exception:
            logger.error(
                "Best-effort re-save after pipeline failure also failed for session %s",
                session_id,
                exc_info=True,
            )
