"""Interview summary generation: one Gemini call over the finished transcript.

`generate_summary` renders the transcript and asks the pro-tier model
(`settings.gemini_pro_model`, through the shared `gemini_client` helper - this
runs after the interview ends so latency doesn't matter and the better
judgment is free) for the Markdown summary defined by
`settings.interview_summary_prompt`. It deliberately raises on every failure
path; the transcripts router is responsible for soft-failing so a summary
failure never loses the saved transcript.
"""

import logging

from app.config import settings
from app.models import TranscriptTurn
from app.services import gemini_client
from app.services.transcript_render import render_transcript

logger = logging.getLogger(__name__)

async def generate_summary(turns: list[TranscriptTurn]) -> str:
    """Generate an interview-focused summary from transcript turns via Gemini's
    OpenAI-compatible chat endpoint, on the pro-tier model (this runs after the
    interview ends, so latency doesn't matter and the better judgment is free).
    Raises on any failure so the caller can persist the transcript with a
    soft-fail summary."""
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured; cannot generate summary.")

    transcript_text = render_transcript(turns)
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
