"""DEV-ONLY testing endpoint — do not ship to production.

Lets a developer run the Data Scout Agent (app.services.data_scout_agent.
run_scout) directly with hand-entered inputs, without running a full
interview through the Host/Evaluator/Coordinator pipeline. Runs ONLY Scout -
nothing else in app.services.pipeline. No interview_id, no InterviewState, no
persistence: this is a pure pass-through harness around the existing
run_scout function, not a re-introduction of the removed routers/scout.py /
ScoutForm / ScoutModal / ScoutPanel feature (which supported saved scout
runs, a modal UI, etc.) - deliberately smaller than that.
"""

import dataclasses
import logging

from fastapi import APIRouter, HTTPException

from app.models import ScoutTestRequest, ScoutTestResponse
from app.services import data_scout_agent

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/scout-test", response_model=ScoutTestResponse)
async def run_scout_test(body: ScoutTestRequest) -> ScoutTestResponse:
    """DEV-ONLY: calls run_scout with the given inputs and returns its full
    result (including the raw representative_verification structure) as
    JSON. `documents_text` (a single pasted document body) is wrapped into
    the `documents: list[str] | None` shape run_scout expects - None when
    blank, matching how a real interview's document upload behaves when
    nothing was attached. `email` is plumbed straight through, exercising
    Representative Identity Verification exactly as app.services.pipeline
    does via VendorProfile.email.

    Wrapped in a broad try/except so a Scout failure (missing API key,
    network issue, malformed response, etc.) comes back as a clean 400/500
    JSON error with the actual message, never an unhandled stack trace."""
    if not body.company_name.strip():
        raise HTTPException(status_code=400, detail="company_name is required to run Scout.")

    documents = [body.documents_text] if body.documents_text.strip() else None

    try:
        result = await data_scout_agent.run_scout(
            body.company_name,
            body.representative_name,
            body.extra_context or None,
            documents,
            body.transcript or None,
            body.email,
        )
    except Exception as exc:
        logger.warning("Scout test run failed: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=500, detail=f"Scout run failed: {type(exc).__name__}: {exc}") from exc

    return ScoutTestResponse(
        internet_findings=result["internet_findings"],
        interview_claims=result["interview_claims"],
        sources=result["sources"],
        findings_ok=result["findings_ok"],
        company_website=result["company_website"],
        # Dataclass -> dict (same conversion app.services.pipeline uses for
        # persistence) - ScoutTestResponse's nested model doesn't validate
        # from an arbitrary object without this.
        representative_verification=dataclasses.asdict(result["representative_verification"]),
        claims_report=result["claims_report"],
    )
