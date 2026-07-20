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
    # Per-domain questionnaires: production assigns each vendor's interview a
    # domain (e.g. "ai_ml"), and `{questionnaires_dir}/{domain}.yaml` is the
    # complete, standalone linear script for that domain. See
    # app.services.interview_config.get_questionnaire/list_domains.
    questionnaires_dir: str = field(
        default_factory=lambda: os.getenv("QUESTIONNAIRES_DIR", "data/questionnaires")
    )
    default_domain: str = field(default_factory=lambda: os.getenv("DEFAULT_DOMAIN", "ai_ml"))
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
    # appends the vendor profile and current question as structured blocks
    # after this text.
    host_system_prompt: str = (
        "You are a professional, friendly AI host conducting a structured "
        "vendor-qualification interview on behalf of a procurement team. You "
        "are given the vendor's profile, the single current question to cover, "
        "and the conversation so far. Phrase the question naturally and "
        "conversationally - never read it verbatim like a script, do not "
        "output markdown, and keep each reply to a few spoken sentences. "
        "Judge whether the vendor's latest message fully answers the current "
        "question: if it does, acknowledge it briefly and then, IN THE SAME "
        "reply, naturally ask the next question given to you (or deliver a "
        "warm closing if there is no next question). Never end a reply with "
        "a bare acknowledgment - the vendor must always hear a question or a "
        "closing, or the conversation stalls. If the answer is not complete, "
        "ask one focused follow-up. The interview flow itself is a fixed "
        "script controlled by the system, not by you - report your judgement "
        "only through the JSON fields described below.\n\n"
        "Always respond with a single JSON object of exactly this shape: "
        '{"reply": "<what you say to the vendor next>", '
        '"answer_complete": <true if the current question is fully answered>, '
        '"profile_updates": {"company_name": <string or null>, '
        '"website": <string or null>, "contact_name": <string or null>, '
        '"contact_role": <string or null>}}. Set each profile_updates field '
        "to the vendor's own words only when they just stated or corrected "
        "that detail this turn; otherwise leave it null."
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

    # System prompt for the Evaluator agent's single holistic scoring call,
    # made once at finalize over the WHOLE transcript (not per answer - a
    # deliberate design choice so early answers are judged in the context of
    # the full conversation). The service appends the rubric categories (ids,
    # names, descriptions) as a structured block after this text; scores are
    # clamped/filtered in code regardless of what comes back.
    evaluator_system_prompt: str = (
        "You are a strict, impartial evaluator assessing a completed "
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
        "short supporting excerpts from the vendor's own words. Independent "
        "research findings may also be provided; weigh the vendor's claims "
        "against them where relevant.\n\n"
        "Always respond with a single JSON object of exactly this shape: "
        '{"categories": {"<category_id>": {"score": <0-5>, '
        '"evidence": ["<short quote>", ...], '
        '"rationale": "<one or two sentences>"}, ...}}'
    )

    # --- Transcript + summary feature ---
    # When set, transcripts persist to this GCS bucket; otherwise they fall back
    # to local JSON files under transcripts_local_dir (dev only, gitignored).
    gcs_bucket: str | None = field(default_factory=lambda: os.getenv("GCS_BUCKET"))
    transcripts_local_dir: str = "transcripts"
    # Gemini's OpenAI-compatible endpoint (same base already used to provision the
    # LiveAvatar LLM config). Reused here for direct summary generation via httpx.
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    # Gemini's NATIVE REST endpoint (not the OpenAI-compat one above) - only this
    # endpoint supports the `google_search` grounding tool, which the Data Scout
    # needs. Kept as its own setting rather than derived by string-munging
    # gemini_base_url, so the two can diverge independently.
    gemini_native_base_url: str = field(
        default_factory=lambda: os.getenv(
            "GEMINI_NATIVE_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/"
        )
    )
    # Fast tier (Host turns) and pro tier (holistic
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

    # Prompt for the Data Scout's single Gemini native-API call with Google
    # Search grounding enabled. Structured output can't be combined with the
    # google_search tool, so the JSON contract is asked for in-prompt and
    # parsed with app.services.llm_json.parse_llm_json instead of a schema.
    # The contract is a JSON *object* wrapping the findings array (not a bare
    # top-level array) so parse_llm_json - which only ever extracts a
    # top-level JSON object, by design - can be reused unmodified; a bare
    # array of objects would otherwise silently decode to just its first
    # element (raw_decode stops at the first complete JSON value it finds).
    scout_research_prompt: str = (
        "Research the following vendor company on the web, using the company "
        "name (and website, if given) below. Cover: company overview; "
        "products/services offered; notable clients or recent news; and any "
        "red flags (disputes, controversies, credibility concerns). Respond "
        "with STRICTLY a single JSON object (no prose, no markdown fences) of "
        'exactly this shape: {"findings": [{"topic": "<short topic label>", '
        '"summary": "<1-3 sentence summary>", "source_url": "<url or null>"}, '
        "...]}. Include 3 to 8 findings, each summary 1 to 3 sentences. If you "
        'genuinely find nothing credible about the company, return {"findings": []}.'
    )


settings = Settings()
