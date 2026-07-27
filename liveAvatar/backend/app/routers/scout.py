import asyncio
import io
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile

from app.models import ScoutRequest, ScoutResponse
from app.services import data_scout_agent, scout_store

logger = logging.getLogger(__name__)

router = APIRouter()

# Document upload policy for the standalone Scout form - independent of the
# intake form's own document handling (app.routers.interview), since this
# tool has no interview_id to attach to. Same extensions/size cap; unlike
# intake, an oversize document here is rejected rather than trimmed (Scout
# claim extraction already truncates its own inputs, see
# data_scout_agent._MAX_DOCUMENT_CHARS).
_SCOUT_DOCUMENT_MAX_FILE_BYTES = 10 * 1024 * 1024
_SCOUT_DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


def _extract_document_text(filename: str, raw: bytes) -> str:
    """Extracts plain text from an uploaded document by extension. Sync
    (pypdf/python-docx are blocking) - the route runs it via
    asyncio.to_thread. Raises ValueError on an unsupported extension or a
    file the parser can't read; both surface as a 400."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in _SCOUT_DOCUMENT_EXTENSIONS:
        raise ValueError(f"Unsupported document type: {suffix or '(none)'}")
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(raw))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        if suffix == ".docx":
            import docx

            document = docx.Document(io.BytesIO(raw))
            return "\n".join(paragraph.text for paragraph in document.paragraphs)
        return raw.decode("utf-8", errors="replace")
    except Exception as e:
        raise ValueError("Could not read the document") from e


@router.post(
    "/api/scout/document",
    response_description="The document's extracted plain text.",
)
async def extract_scout_document(file: UploadFile) -> dict[str, str]:
    """Extracts plain text from one document (.pdf/.docx/.txt/.md) for the
    standalone Scout form to include in ScoutRequest.documents. Stateless -
    nothing is persisted here; the caller collects the returned text and
    sends it along with the rest of the scout request."""
    raw = await file.read()
    if len(raw) > _SCOUT_DOCUMENT_MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Document is too large")
    try:
        text = await asyncio.to_thread(_extract_document_text, file.filename or "", raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not text.strip():
        raise HTTPException(status_code=400, detail="Document appears to be empty")
    return {"text": text}


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
    company_website: str | None = None
    try:
        scout_result = await data_scout_agent.run_scout(
            request.company_name,
            request.representative_name,
            request.representative_role,
            request.extra_context,
            request.documents,
            request.transcript,
        )
        internet_findings = scout_result["internet_findings"]
        interview_claims = scout_result["interview_claims"]
        findings_ok = scout_result["findings_ok"]
        sources = scout_result["sources"]
        company_website = scout_result["company_website"]
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
        # Derived by run_scout - there is no dedicated website field on the request.
        "company_website": company_website,
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
