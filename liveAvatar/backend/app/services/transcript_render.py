"""Shared plain-text transcript rendering for the LLM-facing agents.

Every agent that shows Gemini the conversation (Host per-turn context,
Evaluator whole-transcript scoring, the post-interview summary) renders
turns the same way: one `Label: text` line per non-empty turn. This module
is the single home for that format so the three prompts can never drift
apart; it was previously copy-pasted verbatim in host_agent, evaluator_agent
and summary_service.

Roles outside ROLE_LABELS (e.g. the "system" profile-correction notes
appended by the PATCH profile endpoint) fall back to `role.title()`, so
they render as "System: ..." rather than being dropped.
"""

from app.models import TranscriptTurn

ROLE_LABELS = {"interviewer": "Interviewer", "candidate": "Candidate"}


def render_transcript(turns: list[TranscriptTurn]) -> str:
    """Render turns as one `Label: text` line each, skipping empty turns."""
    lines = []
    for turn in turns:
        label = ROLE_LABELS.get(turn.role, turn.role.title())
        text = turn.text.strip()
        if text:
            lines.append(f"{label}: {text}")
    return "\n".join(lines)
