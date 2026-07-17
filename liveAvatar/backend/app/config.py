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
    max_files: int = 5
    max_file_size_bytes: int = 5 * 1024 * 1024
    max_pdf_pages: int = 10
    interview_base_prompt: str = (
        "You are an experienced technical interviewer assessing a candidate for an "
        "AI Engineering role. Ask them a few simple, basic questions about RAG "
        "(Retrieval-Augmented Generation), fundamentals of Large Language Models (LLMs), "
        "and general Generative AI basics. Keep your responses concise and conversational. "
        "Do not output markdown, speak naturally."
    )

    # --- Resonance multi-agent interview ---
    # Externally reachable base URL of this backend (Cloud Run URL or a dev
    # tunnel). Unset -> legacy mode: sessions fall back to today's
    # Gemini-provisioned behavior so local dev without a tunnel still works.
    public_base_url: str | None = field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL"))
    questionnaire_path: str = field(default_factory=lambda: os.getenv("QUESTIONNAIRE_PATH", "data/questionnaire.yaml"))
    rubric_path: str = field(default_factory=lambda: os.getenv("RUBRIC_PATH", "data/rubric.yaml"))
    scout_enabled: bool = field(default_factory=lambda: os.getenv("SCOUT_ENABLED", "true").lower() != "false")

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
        "question: if it does, acknowledge it briefly and lead into what comes "
        "next; if it does not, ask one focused follow-up. The interview flow "
        "itself is controlled by the system, not by you - report your "
        "judgement only through the JSON fields described below.\n\n"
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

    # System prompt for the Appraiser agent's per-answer Gemini scoring call.
    # The service appends the rubric categories attached to the current
    # question (ids, names, descriptions) as a structured block after this
    # text; scores are clamped/filtered in code regardless of what comes back.
    appraiser_system_prompt: str = (
        "You are a strict, impartial appraiser scoring one vendor answer from "
        "a vendor-qualification interview. You are given the question that was "
        "asked, the vendor's answer, and the rubric categories to score. Score "
        "ONLY the listed categories - never any other category - using an "
        "integer from 0 (no evidence at all) to 5 (excellent, fully "
        "evidenced). Base every score strictly on what the vendor actually "
        "said; do not reward vague claims without substance.\n\n"
        "Always respond with a single JSON object of exactly this shape: "
        '{"category_scores": {"<category_id>": <0-5>, ...}, '
        '"evidence": "<short quote from the answer>", '
        '"rationale": "<one or two sentences>"}'
    )

    # --- Transcript + summary feature ---
    # When set, transcripts persist to this GCS bucket; otherwise they fall back
    # to local JSON files under transcripts_local_dir (dev only, gitignored).
    gcs_bucket: str | None = field(default_factory=lambda: os.getenv("GCS_BUCKET"))
    transcripts_local_dir: str = "transcripts"
    # Gemini's OpenAI-compatible endpoint (same base already used to provision the
    # LiveAvatar LLM config). Reused here for direct summary generation via httpx.
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    gemini_model: str = "gemini-3.5-flash"
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
