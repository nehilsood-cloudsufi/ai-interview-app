"""Interview creation, live-state, and text-chat endpoints for the
interviewer-side UI.

POST creates a fresh in-memory interview with an empty vendor profile - the
intake form is gone, so the profile is captured conversationally by the Host
agent's `intro`/`confirm_profile` onboarding nodes instead. It also picks the
interview's domain-specific questionnaire (`domain` in the optional request
body, defaulting to `settings.default_domain`; an admin assigns this in
production). GET is a read-only snapshot: status, domain, current topic,
Scout insights collected so far, vendor profile, and the post-interview
pipeline's progress (`pipeline_status`/`scorecard`/`recommendation`) once
finalize hands the interview off to `app.services.pipeline` - scoring is
still a single holistic pass that never runs mid-interview, but the UI polls
this endpoint (rather than the finalize response) to learn when it's ready.
POST .../chat is the low-bandwidth text fallback: it drives the same Host
agent turn as the avatar's /llm/{id}/v1/chat/completions gateway, but
same-origin and unauthenticated (no gateway_token in the browser) since it
never leaves our own backend. PATCH .../profile lets the vendor manually correct the Host-captured
profile at any point before the interview is finalized (409s once
`state.pipeline_status` is set, i.e. after /api/transcript/finalize has
handed the interview to the post-interview pipeline); corrected fields are
permanently locked against the Host's LLM-reported profile_updates and a
role="system" note turn is appended so the transcript (and the Evaluator)
sees that a correction happened. Same-origin UI endpoints like the rest of
/api - no auth.
"""

import asyncio
import dataclasses
import io
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile

from app.config import settings
from app.models import (
    ChatRequest,
    ChatResponse,
    CreateInterviewRequest,
    CreateInterviewResponse,
    DomainsResponse,
    InterviewStateResponse,
    TranscriptTurn,
    UpdateProfileRequest,
    UpdateProfileResponse,
    UploadDocumentResponse,
)
from app.services import gemini_client, host_agent, interview_state
from app.services.interview_config import build_question_plan, get_questionnaire, get_rubric, list_domains
from app.services.interview_state import VendorProfile

logger = logging.getLogger(__name__)

router = APIRouter()

# Intake-document policy: oversize documents are TRIMMED to the word limit
# (with a notice to the vendor), never rejected - only unreadable/unsupported
# files or absurd byte sizes error. Plain constants, not env knobs.
_INTAKE_WORD_LIMIT = 3000
_INTAKE_MAX_FILE_BYTES = 10 * 1024 * 1024
_INTAKE_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


def _extract_document_text(filename: str, raw: bytes) -> str:
    """Extract plain text from an uploaded intake document by extension.
    Sync (pypdf/python-docx are blocking) - the route runs it via
    asyncio.to_thread. Raises ValueError on an unsupported extension or a
    file the parser can't read; both surface as a 400."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in _INTAKE_EXTENSIONS:
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


async def _summarize_context(text: str) -> str:
    """Condense intake material (about-text or a document) into the short
    plain-language bullet list the Host and Evaluator consume, via one fast-
    tier Gemini call. Raises on failure - callers soft-fail to "" because
    missing background must never block the interview."""
    payload = {
        "model": settings.gemini_model,
        "messages": [
            {"role": "system", "content": settings.intake_summary_prompt},
            {"role": "user", "content": text},
        ],
        "max_tokens": 800,
    }
    data = await gemini_client.chat_completion(
        payload, timeout=20.0, fallback_model=settings.gemini_model_fallback
    )
    return str(data["choices"][0]["message"]["content"]).strip()

# Maps UpdateProfileRequest/VendorProfile field names to their display label
# in the system-note turn, in canonical (request-shape) order. Clearing a
# required-str field (_REQUIRED_STR_FIELDS) sets "", clearing an optional
# field sets None.
_PROFILE_FIELD_LABELS = {
    "company_name": "Company name",
    "contact_name": "Contact name",
    "contact_role": "Contact role",
}
_REQUIRED_STR_FIELDS = {"company_name", "contact_name"}


@router.get("/api/domains", response_model=DomainsResponse)
async def get_domains():
    """List the interview domains a vendor can be assigned, as a
    `DomainsResponse` holding `domains: [{id, title}]` - one entry per
    questionnaire YAML under the questionnaires directory - plus `default`,
    the domain id the server falls back to when POST /api/interview carries
    no domain (settings.default_domain; the picker preselects it). Takes no
    parameters and never errors (an empty list is valid). In production an
    admin assigns the vendor's domain; the frontend uses this endpoint only
    to populate a dev-stand-in domain picker on the start screen."""
    return {
        "domains": [{"id": domain_id, "title": title} for domain_id, title in list_domains()],
        "default": settings.default_domain,
    }


@router.post("/api/interview", response_model=CreateInterviewResponse)
async def create_interview(body: CreateInterviewRequest | None = None):
    """Create a fresh in-memory interview and return its id.

    This is the first call in the flow - a session (POST /api/session) can
    only be created against an interview_id minted here. The request body
    (`CreateInterviewRequest`) is optional and all its fields are optional:
    `domain` selects the questionnaire (defaults to `settings.default_domain`),
    `tier` is "dev" or "prod" (defaults to "dev"), and for the prod tier
    `passcode` and `duration_minutes` apply. The intake fields pre-fill the
    vendor profile: `contact_name`/`contact_role`/`company_name` are applied
    and locked against the Host's LLM-reported profile_updates (same
    semantics as a manual PATCH edit), and `about_text` is summarized into
    the interview's vendor context (soft-failing to none - a summary hiccup
    never blocks creation). Responds with a `CreateInterviewResponse`
    carrying the new `interview_id`.

    The interview's question plan is also fixed here: every questionnaire
    node normally, or the top-K-by-rubric-weight subset that fits the picked
    duration on the prod tier (see interview_config.build_question_plan).

    Fails with 400 for an unknown `domain` or an unknown `tier`. Prod tier
    adds more gating: 503 if the tier is not configured (PROD_AVATAR_ID and
    DEMO_PASSCODE both required), 403 if `passcode` does not match
    DEMO_PASSCODE, and 400 if `duration_minutes` is outside
    1..(PROD_MAX_SESSION_SECONDS/60). `duration_minutes` is ignored on the
    dev tier.
    """
    domain = (body.domain if body else None) or settings.default_domain
    try:
        get_questionnaire(domain)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown domain: {domain!r}")

    tier = (body.tier if body else None) or "dev"
    if tier not in ("dev", "prod"):
        raise HTTPException(status_code=400, detail=f"Unknown tier: {tier!r}")
    max_session_seconds = None
    if tier == "prod":
        # Prod-tier sessions burn credits (2/min), so they are disabled
        # entirely until both knobs are configured, and gated behind the
        # shared demo passcode after that.
        if not settings.prod_avatar_id or not settings.demo_passcode:
            raise HTTPException(
                status_code=503,
                detail="Production tier is not configured (PROD_AVATAR_ID and DEMO_PASSCODE required)",
            )
        if (body.passcode if body else None) != settings.demo_passcode:
            raise HTTPException(status_code=403, detail="Invalid passcode")

        # Session length picked on the start screen; PROD_MAX_SESSION_SECONDS
        # stays the hard ceiling and 5 minutes the default.
        max_minutes = settings.prod_max_session_seconds // 60
        minutes = body.duration_minutes if body and body.duration_minutes is not None else min(5, max_minutes)
        if not 1 <= minutes <= max_minutes:
            raise HTTPException(
                status_code=400,
                detail=f"duration_minutes must be between 1 and {max_minutes}",
            )
        max_session_seconds = minutes * 60

    # Start-screen intake: pre-fill the profile and lock the provided fields
    # (vendor-typed values beat the Host's LLM-reported profile_updates,
    # exactly like a manual PATCH correction).
    profile = VendorProfile()
    provided_fields = set()
    for field_name in _PROFILE_FIELD_LABELS:
        value = (getattr(body, field_name, None) or "").strip() if body else ""
        if value:
            setattr(profile, field_name, value)
            provided_fields.add(field_name)

    state = interview_state.create(
        profile,
        domain,
        tier,
        max_session_seconds,
        question_plan=build_question_plan(domain, max_session_seconds),
    )
    state.manually_edited_fields |= provided_fields

    about_text = (body.about_text or "").strip() if body else ""
    if about_text:
        try:
            state.vendor_context = await _summarize_context(about_text)
        except Exception:
            logger.warning(
                "Intake about-text summarization failed for interview %s; continuing without it.",
                state.interview_id,
                exc_info=True,
            )

    return {"interview_id": state.interview_id}


@router.post("/api/interview/{interview_id}/document", response_model=UploadDocumentResponse)
async def upload_document(interview_id: str, file: UploadFile):
    """Attach one intake document (.pdf/.docx/.txt/.md) as interview context.

    The file is parsed to text, trimmed to the first 3,000 words when longer
    (`truncated: true` in the response - the frontend shows a short notice;
    oversize documents are never rejected), summarized into plain-language
    bullets, and appended to the interview's vendor context alongside any
    earlier material. Responds with an `UploadDocumentResponse` carrying the
    `filename`, the document's original `word_count`, and the `truncated`
    flag.

    Fails with 404 for an unknown interview, 409 once the interview has been
    finalized, 400 for an unsupported/unreadable/empty document, and 413 over
    10 MB. A summarization failure is soft: the upload still succeeds, the
    context just goes unenriched."""
    state = interview_state.get(interview_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown interview")
    if state.pipeline_status is not None:
        raise HTTPException(status_code=409, detail="Interview already finalized")

    raw = await file.read()
    if len(raw) > _INTAKE_MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Document is too large (10 MB max)")

    try:
        text = await asyncio.to_thread(_extract_document_text, file.filename or "", raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    words = text.split()
    if not words:
        raise HTTPException(status_code=400, detail="Document contains no readable text")

    truncated = len(words) > _INTAKE_WORD_LIMIT
    trimmed_text = " ".join(words[:_INTAKE_WORD_LIMIT])

    try:
        bullets = await _summarize_context(trimmed_text)
    except Exception:
        bullets = ""
        logger.warning(
            "Intake document summarization failed for interview %s (%s); continuing without it.",
            interview_id,
            file.filename,
            exc_info=True,
        )
    if bullets:
        state.vendor_context = f"{state.vendor_context}\n{bullets}".strip()

    return {"filename": file.filename or "", "word_count": len(words), "truncated": truncated}


@router.get("/api/interview/{interview_id}/state", response_model=InterviewStateResponse)
async def get_interview_state(interview_id: str):
    """Read-only snapshot of an interview, keyed by the `interview_id` path
    parameter. Responds with an `InterviewStateResponse`: the interview
    `status`, its `domain`, the `current_topic` (the current questionnaire
    node's topic, or None once it reaches END), the `insights` (Scout
    findings gathered so far), an `updated_at` timestamp, the current
    `vendor_profile`, and the post-interview pipeline fields
    (`pipeline_status`, `scorecard`, `recommendation`) which stay null until
    finalize hands the interview to `app.services.pipeline`. The frontend
    polls this both during the interview (for profile/topic) and after
    finalize (to learn when the scorecard is `ready`). Fails with 404 if the
    interview id is unknown."""
    state = interview_state.get(interview_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown interview")

    node = get_questionnaire(state.domain).get(state.current_node_id)
    profile = state.vendor_profile
    return {
        "status": state.status,
        "domain": state.domain,
        "current_topic": node.topic if node else None,
        "done": state.current_node_id == host_agent.END_NODE_ID,
        "insights": [dataclasses.asdict(finding) for finding in state.scout_findings],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_status": state.pipeline_status,
        "scorecard": dataclasses.asdict(state.scorecard) if state.scorecard is not None else None,
        "recommendation": dataclasses.asdict(state.recommendation) if state.recommendation is not None else None,
        "vendor_profile": {
            # Serializes the profile's three fields (company_name,
            # contact_name, contact_role); matches the finalize route's
            # enrichment.
            "company_name": profile.company_name,
            "contact_name": profile.contact_name,
            "contact_role": profile.contact_role,
        },
    }


@router.post("/api/interview/{interview_id}/chat", response_model=ChatResponse)
async def chat(interview_id: str, body: ChatRequest):
    """Same-origin text-chat fallback for low-bandwidth users: drives the
    identical Host agent conversation as the avatar, without exposing the
    per-interview gateway_token (unlike /llm/{id}/v1, which requires it)."""
    state = interview_state.get(interview_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown interview")

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    if state.status == "created":
        state.status = "active"

    result = await host_agent.handle_turn(state, text, get_questionnaire(state.domain), get_rubric(), mode="chat")

    return {"reply": result.reply, "done": state.current_node_id == host_agent.END_NODE_ID}


def _fmt_profile_value(value: str | None) -> str:
    """Render a profile value for the human-readable system-note turn: a set
    value in double quotes, or the literal "(not set)" for None/empty so a
    cleared field reads sensibly in the note."""
    return f'"{value}"' if value else "(not set)"


@router.patch("/api/interview/{interview_id}/profile", response_model=UpdateProfileResponse)
async def update_profile(interview_id: str, body: UpdateProfileRequest):
    """Vendor-initiated manual correction of the Host-captured profile,
    available at any point before the interview is finalized (409s once
    `state.pipeline_status` is set). Every provided field overwrites
    `state.vendor_profile` and is permanently added to
    `state.manually_edited_fields`, locking it against the Host's
    LLM-reported profile_updates on future turns (re-editing manually is
    always allowed). A role="system" note turn documenting the actual value
    changes is appended to `state.turns` - but only when something actually
    changed, so a no-op PATCH (re-submitting the same value) doesn't spam the
    transcript, even though the field still locks."""
    state = interview_state.get(interview_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown interview")
    if state.pipeline_status is not None:
        raise HTTPException(status_code=409, detail="Interview already finalized")

    provided_fields = [name for name in _PROFILE_FIELD_LABELS if getattr(body, name) is not None]
    if not provided_fields:
        raise HTTPException(status_code=400, detail="At least one profile field must be provided")

    profile = state.vendor_profile
    changes: list[tuple[str, str | None, str | None]] = []
    for field_name in provided_fields:
        stripped = getattr(body, field_name).strip()
        new_value = stripped if stripped else ("" if field_name in _REQUIRED_STR_FIELDS else None)
        old_value = getattr(profile, field_name)
        if new_value != old_value:
            changes.append((_PROFILE_FIELD_LABELS[field_name], old_value, new_value))
        setattr(profile, field_name, new_value)
        state.manually_edited_fields.add(field_name)

    if changes:
        note = "; ".join(
            f"{label}: {_fmt_profile_value(old)} → {_fmt_profile_value(new)}" for label, old, new in changes
        )
        state.turns.append(
            TranscriptTurn(role="system", text=f"[Vendor manually corrected their profile: {note}]")
        )

    return {
        "vendor_profile": {
            "company_name": profile.company_name,
            "contact_name": profile.contact_name,
            "contact_role": profile.contact_role,
        },
        "manually_edited_fields": sorted(state.manually_edited_fields),
    }
