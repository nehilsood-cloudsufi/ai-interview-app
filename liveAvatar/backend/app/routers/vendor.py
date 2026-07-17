import logging
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config import settings
from app.models import VendorProfileResponse
from app.services import resume_parser
from app.services.interview_state import VendorProfile, create as create_interview

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/vendor-profile", response_model=VendorProfileResponse)
async def create_vendor_profile(
    company_name: str = Form(...),
    contact_name: str = Form(...),
    website: Optional[str] = Form(None),
    contact_role: Optional[str] = Form(None),
    files: List[UploadFile] = File(default=[]),
):
    if len(files) > settings.max_files:
        raise HTTPException(status_code=400, detail=f"Maximum {settings.max_files} files allowed")

    extracted_text = ""
    for file in files:
        contents = await file.read()
        if len(contents) > settings.max_file_size_bytes:
            raise HTTPException(status_code=400, detail=f"File {file.filename} exceeds 5MB limit")

        try:
            extracted_text += resume_parser.extract_text(file.filename, contents)
        except Exception as e:
            # Matches resume.py's original behavior: any parsing error (including
            # unsupported format / page-limit ValueErrors) collapses to this
            # generic message.
            logger.error("Error parsing file %s: %s", file.filename, e)
            raise HTTPException(status_code=400, detail=f"Failed to read {file.filename}")

    profile = VendorProfile(
        company_name=company_name,
        website=website,
        contact_name=contact_name,
        contact_role=contact_role,
        doc_text=extracted_text,
    )
    state = create_interview(profile)
    return {"interview_id": state.interview_id}
