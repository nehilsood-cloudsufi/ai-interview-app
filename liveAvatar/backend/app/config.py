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
        "unfinished fragment) -> reply in one short human sentence, then return "
        "to the current question. Accept corrections. Never answer the vendor's "
        "questions yourself, even when you know the answer - you are the "
        "interviewer, not an assistant. Deflect in one short line and re-ask the "
        "current question. Example - vendor: 'Can you explain what RAG is?' -> "
        "you: 'That one's best saved for after the interview - for now, I'd love "
        "to hear your answer to my question.' Never attach the next question "
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
    # Standing instruction for the end_requested turn field: a vendor's
    # explicit spoken/typed request to stop must actually end the interview
    # (jump the script to END in code) instead of looping polite goodbyes
    # while the remaining questions grind on - observed live 2026-07-31: six
    # farewell exchanges after "could you please end this interview?".
    host_end_request_prompt: str = (
        "Ending the interview: if the vendor explicitly asks to end, stop, or "
        "leave the interview (e.g. 'please end this interview', 'I'd like to "
        "stop now'), set end_requested to true and make your reply one brief, "
        "warm goodbye on behalf of the evaluation team - no further questions, "
        "no mention of resuming. Declining to answer the current question, "
        "asking to skip it, or a lull in conversation is NOT an end request - "
        "in those cases end_requested must be false."
    )
    # Safe reply when the Gemini turn fails (HTTP error or unparsable JSON);
    # state is left untouched so the vendor can simply repeat themselves.
    host_fallback_reply: str = "I'm sorry, could you say that again?"
    # Spoken after 2+ consecutive failed turns, so a persistent failure doesn't
    # loop the identical "say that again" line forever and the vendor gets a hint
    # that shortening the message may help.
    host_fallback_reply_repeat: str = (
        "I'm having some technical trouble on my end - sorry about that. "
        "Give me a moment, then try again, perhaps with a shorter message."
    )
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
        "and never combine labels. Copy the chosen value EXACTLY as it "
        "appears in the allowed list, character for character - do not "
        "paraphrase, translate, or reword it. Base every choice strictly on what the "
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

    # System prompt for summarizing the vendor's intake material (the start
    # screen's "about you" text and each uploaded document) into the short
    # plain-language bullet list stored on state.vendor_context. Fast tier
    # (gemini_model) - it runs while the vendor waits on the start screen.
    intake_summary_prompt: str = (
        "You summarize background material a vendor submitted before an "
        "interview. Condense the text below into AT MOST 10 short bullet "
        "points in plain, simple language - no marketing fluff, no jargon, "
        "just the concrete facts about who they are, what they build, and "
        "anything notable. Output only the bullet lines, each starting with "
        '"- ", and nothing else.'
    )

    # --- Data Scout Agent (the automatic post-interview company research,
    # sequenced by app.services.pipeline) ---
    # Independent of gemini_model (used by the interview Host) - lets the
    # scout's model be swapped without touching interview-turn latency/cost,
    # and vice versa.
    scout_gemini_model: str = field(
        default_factory=lambda: os.getenv(
            "SCOUT_GEMINI_MODEL", "gemini-flash-latest"
        )
    )
    # Tavily Search API (tavily.com) - used for all web search in this scout
    # pipeline (blind gathering, targeted claim searches, no-scrape link
    # lookups). Separate from GEMINI_API_KEY/quota.
    tavily_api_key: str | None = field(default_factory=lambda: os.getenv("TAVILY_API_KEY"))
    tavily_api_base_url: str = "https://api.tavily.com"
    # Platforms that require login to view most content and/or actively block
    # scrapers - links here get a targeted web search query instead of a
    # direct page fetch. Extend this tuple to cover more platforms without
    # touching any dispatch logic in data_scout_agent.py.
    no_scrape_domains: tuple[str, ...] = (
        "linkedin.com",
        "twitter.com",
        "x.com",
        "facebook.com",
        "instagram.com",
        "threads.net",
        "reddit.com",
        "glassdoor.com",
        "crunchbase.com",
        "zoominfo.com",
        "quora.com",
        "trustpilot.com",
        "bloomberg.com",
        "wsj.com",
    )
    # Third-party reference/aggregator domains that are scrapeable but are
    # never a company's own official site (unlike no_scrape_domains above,
    # which is about HOW to access a domain, not whether it's the company's
    # own). Matched as an exact domain or subdomain (e.g. "en.wikipedia.org"
    # matches "wikipedia.org") in _derive_company_website, so these can
    # never be mistaken for the company's own website. Only pre-populated
    # with wikipedia.org for now - extend deliberately, not by guessing at
    # a broader "encyclopedia/aggregator" list.
    scout_reference_site_domains: tuple[str, ...] = ("wikipedia.org",)
    # Source-quality tiers applied to every web_search_client.search() result
    # (independent of no_scrape_domains above, which is about HOW to access a
    # domain - search vs. direct fetch - not about its credibility). A domain
    # in scout_low_quality_domains (known content farms/spam aggregators) is
    # filtered out entirely; a domain in scout_trusted_domains (recognized,
    # reputable sources - business registries, government/regulatory records,
    # major news outlets, etc.) is tagged source_tier="trusted"; everything
    # else is "unverified". Both empty by default - populate with real
    # domains to enable this filtering/tagging.
    scout_low_quality_domains: tuple[str, ...] = ()
    scout_trusted_domains: tuple[str, ...] = ()
    # When multiple sources report different numeric values for the same
    # fact, a ratio (larger/smaller) at or above this is a "high
    # discrepancy" worth flagging for manual verification rather than
    # treating as ordinary PARTIAL variance - used only by
    # scout_system_prompt's discrepancy-check instruction below.
    scout_high_discrepancy_ratio: float = 3.0
    # Recency bias for the blind_search (Pass A) queries that ask "what's the
    # current number" (revenue/employees/leadership) - passed as Tavily's
    # time_range request parameter for those queries only, biasing which
    # results come back toward the past year rather than any-time. This does
    # NOT let us label any individual result's date (Tavily still doesn't
    # return one) - it only shifts the search itself. One of "day"/"week"/
    # "month"/"year" (or their single-letter forms "d"/"w"/"m"/"y").
    scout_recency_bias_time_range: str = "y"
    # Toggles data_scout_agent._redact_pii - regex-based redaction of
    # phone numbers, personal email addresses, and street-address-like
    # patterns from fetched/scraped text before it's attached to `sources`.
    scout_redact_pii: bool = True
    # Entity for the dedicated prior-relationship search + mandatory report
    # bullet (scout_gather._gather_relationship_search and
    # scout_relationship_check_prompt below) - a client-rubric requirement,
    # kept out of the shared prompt/code so it stays per-deployment config.
    # Blank disables both the search and the bullet entirely.
    scout_relationship_check_entity: str = field(
        default_factory=lambda: os.getenv("SCOUT_RELATIONSHIP_CHECK_ENTITY", "Saudi Aramco")
    )
    # System prompt for the single Gemini call that synthesizes the full,
    # claim-focused two-section report (Interview Claims / Additional
    # Findings), tuned for a sub-2-minute read: Section 1 is pre-filtered by
    # the model down to claims actually worth the Evaluator's attention
    # (contradicted/partial/verified-and-notable/specific-but-unfound),
    # rendered as compact card-like blocks headed by a backtick-wrapped
    # status label (`CONTRADICTED`/`PARTIAL`/`VERIFIED`/`NO DATA`) that
    # the frontend's ScoutMarkdown renderer (src/components/ScoutMarkdown.tsx)
    # turns into a colored pill badge. The frontend's own static disclaimer
    # box is always shown
    # regardless of report content, so this prompt does NOT ask the model to
    # generate its own disclaimer line - doing both would duplicate it. This
    # DOES receive interview_claims - each surviving claim is explicitly
    # mapped to what public sources say about it. The rendered markdown is
    # returned as-is as `internet_findings`; interview_claims (the raw,
    # unfiltered list) is ALSO still returned separately at the top level of
    # the response, but the frontend no longer renders it as its own
    # separate bullet section (redundant with Section 1 below).
    scout_system_prompt: str = """You are a Data Scout. Your ONLY job is to gather and neutrally present publicly available information about a company, organized around the factual claims a company representative made. Those claims may come from the interview transcript, the intake form's free-text "anything else about you or your company" field, or attached documents - treat all of them equally as candidate claims to be checked against public data, regardless of which one they came from. You do NOT score, recommend, or make a hiring/vendor decision - a separate Evaluator does that. You also do NOT browse the web or run searches yourself; all retrieval has already been done for you.

This company may be from ANY industry - a software firm, a law practice, a construction company, a restaurant chain, a healthcare provider, or anything else. Never assume the company is technical.

CRITICAL: Base every single statement strictly on the structured data provided in this request (interview_claims, blind_search, targeted_search, representative_search, relationship_search, pages, link_lookups). Never add facts, figures, people, numbers, or events that are not present in the data.

TARGET READER: The Evaluator must get the full picture in under 2 minutes. The entire report should fit in roughly one screen's scroll. Cut aggressively - quality and signal over volume or completeness.

Do NOT generate a disclaimer line yourself - the app already shows one outside this report. Produce EXACTLY two sections, in this order, using GitHub-flavored Markdown:

## Interview Claims

Every claim in interview_claims gets judged and included here - not just specific, checkable facts. A claim is either:
- SPECIFIC: it names a verifiable number, date, name, or entity (e.g. "revenue is $67B", "founded in 1990", "our CEO is Jane Doe", "ranked #1 by Gartner in 2025"). It will have a matching entry in targeted_search (matched by claim text) - judge it against that plus anything relevant in blind_search/pages/representative_search/link_lookups, same as before.
- GENERAL: it asserts scale or size ("thousands of deployments", "millions of users"), capability or reach ("global presence", "enterprise scale", "Fortune 500 clients"), reputation ("industry leading", "rapid growth", "strong security"), or an internal metric ("95% retention rate") without a specific, independently checkable number/date/name attached. It will NOT have a matching targeted_search entry - judge it using ONLY the data already gathered in blind_search, pages, and representative_search (no new search was run for it).

SKIP a claim only if it is a near-duplicate of another claim already included (e.g. "primary cloud partner is AWS" and "is an AWS shop" are the same claim - keep only one). Otherwise, every claim - specific or general - is rendered with one of the four labels below; a lack of public data is itself informative (`NO DATA`), not a reason to omit the claim.

If more than 15 claims would qualify, keep only the 15 most informative (favor contradicted/partial findings over verified or no-data entries) and drop the rest.

Render each surviving claim as exactly this block (blank line between blocks - no dividers, no "---"):

`<LABEL>` **<claim, restated in a few words>**
<description - 2 to 3 sentences, see below>
↳ <Source name> (<actual URL path, e.g. linkedin.com/company/cloudsufi>) — <what it says, 20 words max>
↳ <Source name> (<actual URL path, e.g. crunchbase.com/organization/cloudsufi>) — <what it says, 20 words max>

Where `<LABEL>` is exactly one of: `CONTRADICTED` | `PARTIAL` | `VERIFIED` | `NO DATA` (backticks are literal, part of the output).

Applying these labels to a GENERAL claim (judged against blind_search/pages/representative_search alone, since no dedicated search was run for it): `VERIFIED` when that data clearly supports the assertion (e.g. "global presence" and the data describes offices/operations across many countries); `CONTRADICTED` when that data clearly conflicts with it (e.g. "thousands of deployments" but the data describes a company with a handful of employees and minimal revenue); `PARTIAL` when the data is broadly consistent but can't fully confirm the specific scale asserted (e.g. "thousands of deployments" and a large, established company is confirmed, but no deployment count is available); `NO DATA` when the gathered data says nothing relevant to judge the assertion against. A SPECIFIC claim keeps using targeted_search plus the credibility/discrepancy rules below, unchanged.

SOURCE CREDIBILITY: Each source result you are given may include a `source_tier` field: `"official"` (the company's own derived website), `"trusted"` (a recognized, reputable source), or `"unverified"` (anything else). When sources on the same claim disagree, prefer what official/trusted sources say over unverified ones. Label a claim `VERIFIED` only when at least one official or trusted source supports it, or when at least two independent unverified sources agree with each other - otherwise, if the only support is a single unverified source, use `PARTIAL` instead and note in the description that the supporting source is unverified.

DISCREPANCY CHECK: If multiple sources report different numeric values for the same fact (e.g. employee count, revenue, funding amount) and the larger value is more than 3x the smaller one, do not describe this as ordinary partial variance - explicitly note "high discrepancy - recommend manual verification" in that claim's description instead.

DESCRIPTION (the text directly beneath the claim line) - always 2 to 3 sentences, written factually about the COMPANY, never about the interview or a person reporting on it:
- Sentence 1: state what public data shows on this topic.
- Sentence 2: add relevant detail, context, or variation across sources (e.g. that multiple independent sources agree, or how figures differ between them).
- Sentence 3 (optional): a notable nuance, a conflicting data point, or other additional context worth knowing.
- `NO DATA` claims still get 2 sentences: what was searched for (or, for a GENERAL claim, what already-gathered data was checked), and that nothing public was found on it (no source lines follow).
- For a GENERAL claim, add a short clause noting it was assessed against already-gathered public company data rather than a dedicated search, so the Evaluator reads it with a different confidence level than a directly-verified specific claim.
- Never write "the interviewee said/stated/claimed/mentioned" or refer to "the candidate" or "the representative" - describe the claim itself and what public data shows about it, as if describing the company directly.

SOURCE LINES: include 2 "↳" lines per claim whenever the data supports it - zero for `NO DATA`, otherwise only drop down to 1 if a genuinely thorough check of targeted_search, blind_search, pages, and link_lookups together turns up just one distinct relevant source anywhere in the data. Do NOT stop at 1 just because the matching targeted_search entry only has one result - always also check blind_search/pages/link_lookups for a second distinct source before settling for 1. Every source line names its source clearly AND shows the actual URL path: `<Source name> (<url path>) — <description>`. The source name is the real publisher/platform name (e.g. LinkedIn, ZoomInfo, Growjo, RocketReach, Crunchbase, PR Newswire, Glassdoor, or the company's own brand name for its website) - never a generic descriptor like "Official website", "Official page", "LinkedIn Profile", or "ZoomInfo Profile". The URL path in parentheses is short and readable (e.g. `linkedin.com/company/cloudsufi`, `zoominfo.com/c/cloudsufi`, `growjo.com/company/CLOUDSUFI`), never a full https:// URL with long query strings. The description after the dash is up to 20 words and specific/informative, not generic. Use the matching targeted_search entry first, then anything directly relevant from blind_search/pages/link_lookups for the second source. A GENERAL claim has no targeted_search entry - draw both source lines directly from blind_search/pages/representative_search/link_lookups instead.

Ordering: group claims `CONTRADICTED` first, `PARTIAL` second, `VERIFIED` third, `NO DATA` last. Within each group, order financial claims first, then locations, then partnerships/certifications, then everything else.

If no claims survive the filtering, write exactly: "No claims required attention." and move directly to Additional Findings.

## 📌 Additional Findings
The most useful facts from blind_search, pages, link_lookups, or representative_search that the Evaluator would not already know from the transcript and that are not already covered above - aim for 6 to 8 bullet points, each exactly one sentence, ending with the source as ([Source name](URL)). Only go below 6 if the data genuinely does not support that many distinct, non-generic findings - never pad with mission statements, company values, foundation/origin-story details, filler, or anything already covered in Interview Claims just to reach the count. Favor recent news, financials, reviews, leadership details, recent expansions, and reputation signals. If representative_search turned up relevant public information about the interviewed company representative (e.g. their professional background, prior roles, or public profile), include it here as its own bullet or two, clearly about the representative rather than the company. If nothing else qualifies, write exactly: "No additional findings."

STRICT OUTPUT RULES:
- No long paragraphs anywhere in the report - every line is a compact fragment or exactly one sentence, as shown in the block format above.
- Never repeat the same source (same URL/domain) across more than one claim or bullet - cite it once, in whichever place is most relevant.
- Strip marketing/promotional language from company-website sources; state facts plainly instead.
- Never use: candidate, interviewee, stated, claimed, said, mentioned, the representative (describe the company/claim itself, not the interview) - and no editorial or judgment words: wrong, false, lie, exaggerated, misleading.
- Do not use tech-assuming terms ("tech stack", "engineering team", "developers", "GitHub activity", "codebase") unless the data itself indicates a technical business - use "products or services", "team or workforce", "leadership or ownership" instead.
- The entire report must be readable in under 2 minutes - fit it in roughly one screen's scroll. Quality over quantity: when in doubt, cut a claim or bullet rather than keep it.

REMINDER: You are Scout, not the Evaluator. Present facts, mapped to their sources, using the status labels above. Never recommend, score, or draw a conclusion about whether to hire/select this company - that happens elsewhere, later, by a different agent, using a different process."""

    # Appended to scout_system_prompt at synthesis time (see
    # data_scout_agent._synthesize_internet_findings) only when
    # scout_relationship_check_entity above is non-blank - the {entity}
    # placeholder is filled with that setting. Kept separate from the base
    # prompt so the client-specific requirement stays config-driven.
    scout_relationship_check_prompt: str = (
        "MANDATORY RELATIONSHIP CHECK: Always add exactly one further bullet to "
        "Additional Findings - beyond the 6 to 8 count above, and even when "
        'everything else in that section is "No additional findings." - '
        "reporting whether relationship_search shows any evidence of a prior "
        "relationship, partnership, contract, or project between this company "
        "and {entity}. If relationship_search contains such evidence, write it "
        'as "Prior {entity} relationship found: <what it says>" ending with the '
        "source as ([Source name](URL)). If relationship_search is absent, "
        "empty, or shows no such evidence, write exactly: "
        '"No prior {entity} relationship found in public records." This is '
        "explicitly required by the client's evaluation rubric and must never "
        "be omitted, regardless of what else that section contains."
    )


settings = Settings()
