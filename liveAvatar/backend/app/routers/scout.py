import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from app.models import ScoutRequest, ScoutResponse
from app.services import data_scout_agent, scout_store

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/api/scout",
    response_model=ScoutResponse,
    response_description="The synthesized scout report, plus the raw sources gathered while producing it.",
)
async def scout_company(request: ScoutRequest) -> dict[str, Any]:
    """Runs the Data Scout Agent for a company and persists the resulting
    report. Returns the synthesized findings alongside the raw gathered
    sources, even if synthesis itself failed (see ScoutResponse.findings_ok)."""
    scout_id = uuid.uuid4().hex

    internet_findings = ""
    interview_claims: list[str] = []
    findings_ok = True
    sources: dict[str, Any] = {}
    try:
        scout_result = await data_scout_agent.run_scout(
            request.company_name,
            request.company_website,
            request.representative_name,
            request.representative_role,
            request.transcript,
        )
        internet_findings = scout_result["internet_findings"]
        interview_claims = scout_result["interview_claims"]
        findings_ok = scout_result["findings_ok"]
        sources = scout_result["sources"]
    except Exception as exc:
        # run_scout itself already isolates the (much more likely) synthesis
        # failure and still returns gathered sources - this only catches a
        # genuinely unexpected failure in the gathering pipeline itself, in
        # which case sources really is unavailable.
        findings_ok = False
        logger.warning("Scout data gathering failed unexpectedly for %s: %s", scout_id, exc)

    scout_record = {
        "scout_id": scout_id,
        "company_name": request.company_name,
        "company_website": request.company_website,
        "representative_name": request.representative_name,
        "representative_role": request.representative_role,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
        "internet_findings": internet_findings,
        "interview_claims": interview_claims,
        "findings_ok": findings_ok,
    }

    try:
        await scout_store.save(scout_id, scout_record)
    except Exception as exc:
        logger.error("Failed to persist scout report %s: %s", scout_id, exc)
        raise HTTPException(status_code=500, detail="Failed to save scout report")

    return {
        "scout_id": scout_id,
        "internet_findings": internet_findings,
        "interview_claims": interview_claims,
        "sources": sources,
        "findings_ok": findings_ok,
    }


@router.get(
    "/api/scout/{scout_id}",
    response_description="The previously saved scout report record.",
)
async def get_scout_report(scout_id: str) -> dict[str, Any]:
    """Retrieves a previously saved scout report by id. Returns the persisted
    record (a superset of ScoutResponse's fields, including company_name/
    company_website/representative_name/representative_role/created_at)."""
    record = await scout_store.get(scout_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Scout report not found")
    return record
