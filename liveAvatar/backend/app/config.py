"""Central configuration: every env var, secret, and tuning constant the app
reads lives on the frozen `Settings` dataclass instantiated as the module-level
`settings` singleton, so nothing elsewhere calls `os.getenv` directly.

`backend/.env` is loaded first with override=True, so values committed to
`.env` win over anything already exported in the shell (this avoids a stale
`GEMINI_API_KEY` in the environment silently shadowing the intended one). Each
field's `default_factory` reads its env var at construction time; fields
without a factory are fixed constants (not env-overridable). Secrets/URLs
(API keys, `PUBLIC_BASE_URL`, avatar ids, `GCS_BUCKET`) come from the
environment, while the long prompt strings and timing thresholds are defaults
here that can still be overridden where a factory exposes them. See
`backend/.env.example` for the env vars and CLAUDE.md for how each is used.
"""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# override=True makes backend/.env authoritative over any pre-set shell
# environment variables (e.g. a stale GEMINI_API_KEY exported in the shell),
# so the app always uses the keys/config committed to .env.
load_dotenv(override=True)

# HeyGen's free sandbox avatar ("Wayne"), used by dev-tier interviews. Only
# valid with is_sandbox=True - pairing it with is_sandbox=False makes LiveKit
# time out silently (see docs/KT.md).
SANDBOX_AVATAR_ID = "dd73ea75-1218-4ef3-92ce-606d5f7fbc0a"


@dataclass(frozen=True)
class Settings:
    """All application configuration in one immutable place; the module
    exposes a single instance as `settings`. Grouped by concern via the
    section comments below: core LiveAvatar/Gemini credentials and URLs, the
    production-tier avatar knobs, the time-aware wrap-up thresholds, the Host
    and Evaluator system prompts, and the transcript/summary/Scout settings.
    Frozen so config is read-only at runtime; each `field(default_factory=...)`
    pulls from the environment at startup (env-overridable), while plain
    defaults are fixed constants."""

    liveavatar_api_key: str | None = field(default_factory=lambda: os.getenv("LIVEAVATAR_API_KEY"))
    gemini_api_key: str | None = field(default_factory=lambda: os.getenv("GEMINI_API_KEY"))
    liveavatar_base_url: str = "https://api.liveavatar.com/v1"
    avatar_id: str = SANDBOX_AVATAR_ID

    # --- Production tier (/prod URL; interviews with tier="prod") ---
    # Sandbox (dev-tier) sessions are free but auto-terminate after ~1 minute;
    # prod-tier sessions use this avatar with is_sandbox=false and burn
    # credits (2/minute). Both PROD_AVATAR_ID and DEMO_PASSCODE must be set or
    # prod-tier interview creation is rejected.
    prod_avatar_id: str | None = field(default_factory=lambda: os.getenv("PROD_AVATAR_ID"))
    # Optional voice override; public video avatars come with a default voice.
    prod_voice_id: str | None = field(default_factory=lambda: os.getenv("PROD_VOICE_ID"))
    # Shared passcode required to create a prod-tier interview, so a leaked
    # URL can't burn credits.
    demo_passcode: str | None = field(default_factory=lambda: os.getenv("DEMO_PASSCODE"))
    # Hard cap per prod-tier session (HeyGen's max_session_duration), bounding
    # the worst-case credit spend of a single session.
    prod_max_session_seconds: int = field(
        default_factory=lambda: int(os.getenv("PROD_MAX_SESSION_SECONDS", "600"))
    )

    # --- Time-aware wrap-up (avatar sessions with a clock, i.e. prod tier) ---
    # The Host paces the interview against state.max_session_seconds so the
    # session never ends mid-sentence; HeyGen's hard cap gets this much grace
    # beyond the picked duration and acts purely as a safety net.
    prod_session_grace_seconds: int = 60
    # Under this many remaining seconds: no more follow-ups (answers are
    # accepted as complete) and replies are kept short via the prompt below.
    host_time_pressure_seconds: int = 120
    # Under this many remaining seconds: skip whatever questions remain and
    # deliver the canned closing while the stream is still alive.
    host_wrapup_seconds: int = 60
    # Appended to the system prompt during the time-pressure window.
    host_time_pressure_prompt: str = (
        "The session is nearly out of time. Keep your reply to one or two "
        "short sentences, accept the vendor's answer as complete rather than "
        "asking follow-ups, and move briskly to the next question."
    )
    # Spoken (without an LLM call) when the wrap-up threshold is reached.
    host_timeup_reply: str = (
        "I'm afraid we're right at time for today, so let's pause here. "
        "Thank you for walking me through everything - our evaluation team "
        "will review the conversation and follow up with next steps."
    )

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
    default_domain: str = field(default_factory=lambda: os.getenv("DEFAULT_DOMAIN", "frontier_tech"))
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
        "You are Noor, a friendly professional host running a structured "
        "vendor-qualification interview. You are given the vendor's profile, "
        "the current question, and the conversation so far. Speak naturally: "
        "no markdown, a few short sentences, never re-confirm what was "
        "already confirmed.\n\n"
        "Judge the vendor's latest message:\n"
        "- Fully answers the current question -> acknowledge in a few words "
        "and, in the same reply, ask the next question given to you (or give "
        "a warm closing if there is none). They must always hear a question "
        "or a closing here.\n"
        "- A genuine but thin attempt -> ask one focused follow-up.\n"
        "- Not an answer (a correction, a question to you, small talk, an "
        "unfinished fragment) -> reply in one short human sentence (accept "
        "corrections, defer off-topic questions until after the interview), "
        "then return to the current question. Never attach the next question "
        "here.\n\n"
        "The script is controlled by the system, not you - report your "
        "judgement only via the JSON. Always respond with a single JSON "
        'object: {"reply": "<what you say next>", '
        '"answer_complete": <true only if the current question is fully answered>, '
        '"profile_updates": {"company_name": <string or null>, '
        '"contact_name": <string or null>, '
        '"contact_role": <string or null>}}. Set a profile_updates field '
        "only when the vendor just stated or corrected it; otherwise null."
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
    # Appended to host_system_prompt only when the Host is driving the
    # text-chat fallback (mode="chat" in host_agent.handle_turn/stream_turn).
    # Per the 2026-07-20 meeting: typed answers are terse, so the avatar-mode
    # prompt's "ask one focused follow-up" instinct must not fire on short but
    # complete typed answers.
    host_chat_mode_prompt: str = field(
        default_factory=lambda: os.getenv(
            "HOST_CHAT_MODE_PROMPT",
            "The vendor is typing in a text chat, not speaking. Treat concise "
            "answers as complete rather than pressing for elaboration, and "
            "keep your own replies brief. If a detail was already stated "
            "earlier, infer it and confirm it instead of re-asking (for "
            "example: 'You mentioned GCP earlier - do you support other "
            "clouds too?').",
        )
    )
    # How long the gateway lets an utterance "settle" before processing it:
    # HeyGen's VAD fires on short pauses, so a fragment is only treated as
    # final if no newer fragment supersedes it within this window. The beat a
    # human interviewer waits before answering. Trade-off: adds this much
    # latency to every genuine turn (HeyGen's own read timeout is 10s).
    host_utterance_settle_seconds: float = field(
        default_factory=lambda: float(os.getenv("HOST_UTTERANCE_SETTLE_SECONDS", "0.5"))
    )
    # With more than this many seconds left on a clocked interview, the Host
    # is told to dig deeper instead of accepting brief answers - the inverse
    # of host_time_pressure_seconds, so a 5-minute booking actually spends
    # its time interviewing (observed 2026-07-22: a 5-min session finished in
    # 3 because every surface answer was accepted and the script just ended).
    host_time_generous_seconds: int = field(
        default_factory=lambda: int(os.getenv("HOST_TIME_GENEROUS_SECONDS", "180"))
    )
    # Appended to the system prompt while time is generous (see above).
    host_time_generous_prompt: str = field(
        default_factory=lambda: os.getenv(
            "HOST_TIME_GENEROUS_PROMPT",
            "There is ample time remaining in this interview. When the "
            "vendor's answer is brief or stays at the surface, do not accept "
            "it immediately: mark it incomplete and ask one focused follow-up "
            "that digs a level deeper - a concrete example, a how, or a why. "
            "Move on once they have added real substance.",
        )
    )
    # Appended to host_system_prompt only in avatar mode. HeyGen's VAD splits
    # flowing speech at pauses, so one spoken answer can arrive as several
    # partial utterances ("Actually, we are working on AI services. We
    # provide" / "and" / ...). Judging such a fragment as a complete answer
    # burns script questions the vendor never heard - seen live 2026-07-22:
    # five questions consumed in 46 seconds.
    host_avatar_mode_prompt: str = field(
        default_factory=lambda: os.getenv(
            "HOST_AVATAR_MODE_PROMPT",
            "The vendor is speaking; transcription may cut them off "
            "mid-thought or mangle names. A fragment that ends mid-sentence "
            "is not an answer - just invite them to continue ('Go on, I'm "
            "listening.'). Accept name corrections the first time.",
        )
    )

    # System prompt for the Evaluator agent's single holistic scoring call,
    # made once at finalize over the WHOLE transcript (not per answer - a
    # deliberate design choice so early answers are judged in the context of
    # the full conversation). The service appends the rubric categories (ids,
    # names, descriptions, and each category's fixed allowed values) as a
    # structured block after this text; chosen labels are resolved to points
    # and filtered in code regardless of what comes back.
    evaluator_system_prompt: str = (
        "You are a strict, impartial evaluator assessing a completed "
        "vendor-qualification interview. You are given the full interview "
        "transcript and the rubric categories to score, each with a FIXED "
        "list of allowed values. Judge the interview as a whole: weigh "
        "everything the vendor said across the entire conversation, not any "
        "single answer in isolation. For each category, choose EXACTLY ONE "
        "value from that category's allowed list - never invent a new label "
        "and never combine labels. Base every choice strictly on what the "
        "vendor actually said; do not reward vague claims without substance. "
        "If a category was never meaningfully discussed in the interview, "
        "OMIT it entirely rather than guessing a value. For each scored "
        "category, quote one to three short supporting excerpts from the "
        "vendor's own words. Independent research findings may also be "
        "provided; weigh the vendor's claims against them where relevant.\n\n"
        "Always respond with a single JSON object of exactly this shape: "
        '{"categories": {"<category_id>": {"value": "<one of that '
        'category\'s allowed values>", '
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
        "name below. Cover: company overview; "
        "products/services offered; notable clients or recent news; and any "
        "red flags (disputes, controversies, credibility concerns). Respond "
        "with STRICTLY a single JSON object (no prose, no markdown fences) of "
        'exactly this shape: {"findings": [{"topic": "<short topic label>", '
        '"summary": "<1-3 sentence summary>", "source_url": "<url or null>"}, '
        "...]}. Include 3 to 8 findings, each summary 1 to 3 sentences. If you "
        'genuinely find nothing credible about the company, return {"findings": []}.'
    )


settings = Settings()
