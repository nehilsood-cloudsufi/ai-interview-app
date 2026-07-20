import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    liveavatar_api_key: str | None = field(default_factory=lambda: os.getenv("LIVEAVATAR_API_KEY"))
    gemini_api_key: str | None = field(default_factory=lambda: os.getenv("GEMINI_API_KEY"))
    liveavatar_base_url: str = "https://api.liveavatar.com/v1"
    avatar_id: str = "dd73ea75-1218-4ef3-92ce-606d5f7fbc0a"

    # --- Resonance multi-agent interview ---
    # Externally reachable base URL of this backend (Cloud Run URL or a dev
    # tunnel), so HeyGen can call back into /llm/{interview_id}/v1. Required
    # for session creation - gateway mode is the only mode.
    public_base_url: str | None = field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL"))
    questionnaire_path: str = field(default_factory=lambda: os.getenv("QUESTIONNAIRE_PATH", "data/questionnaire.yaml"))
    rubric_path: str = field(default_factory=lambda: os.getenv("RUBRIC_PATH", "data/rubric.yaml"))
    scout_enabled: bool = field(default_factory=lambda: os.getenv("SCOUT_ENABLED", "true").lower() != "false")
    # Optional latency polish: when enabled, the gateway streams the Host's
    # reply to HeyGen token-by-token (avatar starts speaking sooner) instead of
    # emitting the whole reply in one chunk. Default off so production behavior
    # is unchanged until explicitly turned on.
    host_streaming_enabled: bool = field(
        default_factory=lambda: os.getenv("HOST_STREAMING_ENABLED", "false").lower() in ("1", "true", "yes")
    )

    # System prompt for the Host agent's per-turn Gemini call. The service
    # appends the vendor profile, current question, and any Scout intel as
    # structured blocks after this text.
    host_system_prompt: str = (
        "You are a professional, friendly AI host conducting a structured "
        "vendor-qualification interview on behalf of a procurement team. You "
        "are given the vendor's profile, the single current question to cover, "
        "and the conversation so far. Phrase the question naturally and "
        "conversationally - never read it verbatim like a script, do not "
        "output markdown, and keep each reply to a few spoken sentences. "
        "Judge whether the vendor's latest message fully answers the current "
        "question: if it does, acknowledge it briefly and then, IN THE SAME "
        "reply, naturally ask the next question listed for the branch signal "
        "you chose (or deliver a warm closing if that branch ends the "
        "interview). Never end a reply with a bare acknowledgment - the "
        "vendor must always hear a question or a closing, or the conversation "
        "stalls. If the answer is not complete, ask one focused follow-up. "
        "The interview flow itself is controlled by the system, not by you - "
        "report your judgement only through the JSON fields described below.\n\n"
        "Always respond with a single JSON object of exactly this shape: "
        '{"reply": "<what you say to the vendor next>", '
        '"answer_complete": <true if the current question is fully answered>, '
        '"branch_signal": "<one of the allowed branch signals for the current '
        'question>"}'
    )
    # Spoken by the Host without an LLM call once the interview has already
    # reached the END node.
    host_closing_reply: str = (
        "Thanks again for your time today - the interview is complete, and our "
        "evaluation team will follow up with next steps."
    )
    # Safe reply when the Gemini turn fails (HTTP error or unparsable JSON);
    # state is left untouched so the vendor can simply repeat themselves.
    host_fallback_reply: str = "I'm sorry, could you say that again?"

    # System prompt for the Appraiser agent's single holistic scoring call,
    # made once at finalize over the WHOLE transcript (not per answer - a
    # deliberate design choice so early answers are judged in the context of
    # the full conversation). The service appends the rubric categories (ids,
    # names, descriptions) as a structured block after this text; scores are
    # clamped/filtered in code regardless of what comes back.
    appraiser_system_prompt: str = (
        "You are a strict, impartial appraiser evaluating a completed "
        "vendor-qualification interview. You are given the full interview "
        "transcript and the rubric categories to score. Judge the interview "
        "as a whole: weigh everything the vendor said across the entire "
        "conversation, not any single answer in isolation. Score ONLY the "
        "listed categories - never any other category - using an integer "
        "from 0 (no evidence at all) to 5 (excellent, fully evidenced). Base "
        "every score strictly on what the vendor actually said; do not "
        "reward vague claims without substance. If a category was never "
        "meaningfully discussed in the interview, OMIT it entirely rather "
        "than guessing a score. For each scored category, quote one to three "
        "short supporting excerpts from the vendor's own words.\n\n"
        "Always respond with a single JSON object of exactly this shape: "
        '{"categories": {"<category_id>": {"score": <0-5>, '
        '"evidence": ["<short quote>", ...], '
        '"rationale": "<one or two sentences>"}, ...}}'
    )

    # System prompt for the Coordinator agent's invite-drafting Gemini call.
    # The service appends the vendor profile, the follow-up recommendation,
    # the focus categories with supporting evidence quotes, and any Scout
    # findings as structured blocks after this text.
    coordinator_invite_prompt: str = (
        "You are a coordinator preparing a follow-up meeting package after a "
        "vendor-qualification interview, on behalf of a procurement team. You "
        "are given the vendor's profile, the follow-up recommendation (advance "
        "to a next-round deep-dive, or clarify weak areas), the focus "
        "categories with supporting evidence from the interview, and any "
        "research findings. Draft a concise, professional meeting package: a "
        "short meeting title, a focused agenda covering the focus categories, "
        "a sensible meeting duration in minutes, and an invitation email "
        "addressed to the vendor contact by name. The email must be "
        "professional and brief, and its body must be plain text with no "
        "markdown formatting.\n\n"
        "Always respond with a single JSON object of exactly this shape: "
        '{"title": "<meeting title>", "agenda": ["<agenda item>", ...], '
        '"duration_minutes": <integer>, "email_draft": "<plain-text email>"}'
    )

    # --- Transcript + summary feature ---
    # When set, transcripts persist to this GCS bucket; otherwise they fall back
    # to local JSON files under transcripts_local_dir (dev only, gitignored).
    gcs_bucket: str | None = field(default_factory=lambda: os.getenv("GCS_BUCKET"))
    transcripts_local_dir: str = "transcripts"
    # Gemini's OpenAI-compatible endpoint (same base already used to provision the
    # LiveAvatar LLM config). Reused here for direct summary generation via httpx.
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    # Fast tier (Host turns, Coordinator drafting) and pro tier (holistic
    # scoring + summary at finalize, where latency doesn't matter). Both use
    # Gemini's auto-tracking "-latest" aliases so we stop hand-bumping
    # versions; the pinned *_fallback names are retried automatically by
    # gemini_client when an alias stops resolving (Google hot-swaps aliases
    # with only 2 weeks' email notice). All four are env-overridable.
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-flash-latest"))
    gemini_model_fallback: str = field(
        default_factory=lambda: os.getenv("GEMINI_MODEL_FALLBACK", "gemini-3.5-flash")
    )
    gemini_pro_model: str = field(default_factory=lambda: os.getenv("GEMINI_PRO_MODEL", "gemini-pro-latest"))
    gemini_pro_model_fallback: str = field(
        default_factory=lambda: os.getenv("GEMINI_PRO_MODEL_FALLBACK", "gemini-3.1-pro-preview")
    )
    interview_summary_prompt: str = (
        "You are an assistant that writes concise, factual notes from a technical "
        "interview transcript. The transcript labels the AI interviewer as "
        "'Interviewer' and the human candidate as 'Candidate'. Base every statement "
        "only on what was actually said — do not invent details. Output GitHub-"
        "flavored Markdown with exactly these sections, in this order:\n\n"
        "## Topics Covered\n## Candidate Strengths\n## Areas of Concern / Gaps\n"
        "## Notable Answers\n## Overall Recap\n\n"
        "Use short bullet points under each heading (a sentence or two each). If a "
        "section has nothing to report from the transcript, write '- N/A'. Keep the "
        "whole summary tight and scannable."
    )


settings = Settings()
