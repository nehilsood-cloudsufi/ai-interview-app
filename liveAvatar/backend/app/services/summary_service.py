import logging

from app.config import settings
from app.models import TranscriptTurn
from app.services import gemini_client

logger = logging.getLogger(__name__)

_ROLE_LABELS = {"interviewer": "Interviewer", "candidate": "Candidate"}


def _render_transcript(turns: list[TranscriptTurn]) -> str:
    lines = []
    for turn in turns:
        label = _ROLE_LABELS.get(turn.role, turn.role.title())
        text = turn.text.strip()
        if text:
            lines.append(f"{label}: {text}")
    return "\n".join(lines)


async def generate_summary(turns: list[TranscriptTurn]) -> str:
    """Generate an interview-focused summary from transcript turns via Gemini's
    OpenAI-compatible chat endpoint, on the pro-tier model (this runs after the
    interview ends, so latency doesn't matter and the better judgment is free).
    Raises on any failure so the caller can persist the transcript with a
    soft-fail summary."""
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured; cannot generate summary.")

    transcript_text = _render_transcript(turns)
    if not transcript_text:
        raise ValueError("Transcript is empty; nothing to summarize.")

    payload = {
        "model": settings.gemini_pro_model,
        "messages": [
            {"role": "system", "content": settings.interview_summary_prompt},
            {"role": "user", "content": transcript_text},
        ],
    }

    data = await gemini_client.chat_completion(
        payload, timeout=60.0, fallback_model=settings.gemini_pro_model_fallback
    )
    return data["choices"][0]["message"]["content"].strip()
