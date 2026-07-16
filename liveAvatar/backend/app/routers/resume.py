import logging
from typing import List, Optional

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config import settings
from app.dependencies import resolve_api_key
from app.models import UploadResumeResponse
from app.services import resume_parser
from app.services.liveavatar_client import create_context

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/upload-resume", response_model=UploadResumeResponse)
async def upload_resume(
    files: List[UploadFile] = File(...),
    api_key: Optional[str] = Form(None),
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
            # Matches original behavior: any parsing error (including unsupported
            # format / page-limit ValueErrors) collapses to this generic message.
            logger.error("Error parsing file %s: %s", file.filename, e)
            raise HTTPException(status_code=400, detail=f"Failed to read {file.filename}")

    full_prompt = f"{settings.interview_base_prompt}\n\nCandidate's Additional Context (Resume/Portfolio):\n{extracted_text}"

    liveavatar_key = resolve_api_key(api_key)
    if not liveavatar_key:
        raise HTTPException(status_code=500, detail="LiveAvatar API Key missing")

    try:
        context_id = await create_context(liveavatar_key, full_prompt)
        return {"context_id": context_id}
    except httpx.HTTPStatusError as e:
        logger.error("LiveAvatar Context Error: %s", e.response.text)
        raise HTTPException(status_code=e.response.status_code, detail="Failed to create context")
