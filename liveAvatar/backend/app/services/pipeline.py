"""Post-interview background pipeline: sequences Scout -> Evaluator ->
Coordinator after an interview finishes, owns `state.pipeline_status`, and
persists the final transcript record. This module is THE ONLY orchestrator -
agents never call each other, only this module composes them.
"""

import asyncio
import dataclasses
import logging

from app.services import coordinator_agent, evaluator_agent, scout_agent, transcript_store
from app.services.interview_config import get_rubric
from app.services.interview_state import InterviewState

logger = logging.getLogger(__name__)

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
        findings = await scout_agent.run(state)  # never raises, by contract

        state.pipeline_status = "evaluating"
        scorecard = None
        try:
            scorecard = await evaluator_agent.score_interview(
                state.turns, rubric, state.scout_findings, vendor_context=state.vendor_context
            )
        except Exception as e:
            logger.warning("Holistic scoring failed for session %s: %s", session_id, e)

        rec = None
        if scorecard is not None and scorecard.overall is not None:
            rec = coordinator_agent.evaluate_followup(scorecard, rubric)

        state.scorecard = scorecard
        state.recommendation = rec

        payload["scout_findings"] = [dataclasses.asdict(finding) for finding in findings]
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
