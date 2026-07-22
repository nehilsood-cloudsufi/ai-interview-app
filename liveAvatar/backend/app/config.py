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
        "closing, or the conversation stalls. Keep acknowledgments to a few "
        "words and never repeat or re-confirm information that was already "
        "confirmed earlier in the conversation - a human interviewer says "
        "things once. If the answer is not complete, "
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

    # --- Data Scout Agent (on-demand, company_name/website/transcript input) ---
    # Independent of gemini_model (used by the interview Host) - lets the
    # scout's model be swapped without touching interview-turn latency/cost,
    # and vice versa.
    scout_gemini_model: str = field(
        default_factory=lambda: os.getenv(
            "SCOUT_GEMINI_MODEL", "gemini-2.0-flash"
        )
    )
    # Optional GitHub token: raises the unauthenticated rate limit (60/hr) to
    # 5000/hr. The public API works without it, just at a lower rate limit.
    github_api_token: str | None = field(default_factory=lambda: os.getenv("GITHUB_API_TOKEN"))
    github_api_base_url: str = "https://api.github.com"
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
    )
    # When set, scout reports persist to this GCS bucket (same bucket as
    # transcripts works fine - different blob prefix); otherwise local JSON
    # files under scout_local_dir (dev only, gitignored).
    scout_local_dir: str = "scout_reports"
    # System prompt for the single Gemini call that synthesizes the full,
    # claim-focused two-section report (Interview Claims / Additional
    # Findings), tuned for a sub-2-minute read: Section 1 is pre-filtered by
    # the model down to claims actually worth the Evaluator's attention
    # (contradicted/partial/verified-and-notable/specific-but-unfound),
    # rendered as compact card-like blocks headed by a backtick-wrapped
    # status label (`CONTRADICTED`/`PARTIAL`/`VERIFIED`/`NO DATA`) that
    # src/components/ScoutPanel.tsx's markdown renderer turns into a colored
    # pill badge. The frontend's own static disclaimer box is always shown
    # regardless of report content, so this prompt does NOT ask the model to
    # generate its own disclaimer line - doing both would duplicate it. This
    # DOES receive interview_claims - each surviving claim is explicitly
    # mapped to what public sources say about it. The rendered markdown is
    # returned as-is as `internet_findings`; interview_claims (the raw,
    # unfiltered list) is ALSO still returned separately at the top level of
    # the response, but the frontend no longer renders it as its own
    # separate bullet section (redundant with Section 1 below).
    scout_system_prompt: str = """You are a Data Scout. Your ONLY job is to gather and neutrally present publicly available information about a company, organized around the factual claims a company representative made in an interview. You do NOT score, recommend, or make a hiring/vendor decision - a separate Evaluator does that. You also do NOT browse the web or run searches yourself; all retrieval has already been done for you.

This company may be from ANY industry - a software firm, a law practice, a construction company, a restaurant chain, a healthcare provider, or anything else. Never assume the company is technical.

CRITICAL: Base every single statement strictly on the structured data provided in this request (interview_claims, blind_search, targeted_search, pages, github, link_lookups). Never add facts, figures, people, numbers, or events that are not present in the data.

TARGET READER: The Evaluator must get the full picture in under 2 minutes. The entire report should fit in roughly one screen's scroll. Cut aggressively - quality and signal over volume or completeness.

Do NOT generate a disclaimer line yourself - the app already shows one outside this report. Produce EXACTLY two sections, in this order, using GitHub-flavored Markdown:

## Interview Claims

First, filter interview_claims down to only the ones worth the Evaluator's attention. INCLUDE a claim only if it is:
- Contradicted by public data.
- Partially supported, with a notable difference (e.g. a materially different number, date, or location).
- A specific, business-relevant fact (e.g. a leadership title, certification, ranking, headquarters location) that public sources independently confirm.
- About something specific and verifiable (a number, date, location, ranking, partnership, or certification) for which no public data exists at all.

SKIP a claim if it is:
- A generic service/capability description any company or consultant in the space would plausibly make, with no meaningful public data either way (this is different from a specific, confirmable fact - skip "we offer data engineering services", but keep "our CEO is Jane Doe" if sources confirm it).
- Vague or inherently private (e.g. retention rate, satisfaction score, internal growth percentage) - these are almost never publicly available for any company, so their absence is not meaningful signal.
- A near-duplicate of another claim already included (e.g. "primary cloud partner is AWS" and "is an AWS shop" are the same claim - keep only one).

If more than 15 claims would qualify, keep only the 15 most informative (favor contradicted/partial findings over verified or no-data entries) and drop the rest.

Render each surviving claim as exactly this block (blank line between blocks - no dividers, no "---"):

`<LABEL>` **<claim, restated in a few words>**
<description - 2 to 3 sentences, see below>
↳ <Source name> (<actual URL path, e.g. linkedin.com/company/cloudsufi>) — <what it says, 20 words max>
↳ <Source name> (<actual URL path, e.g. crunchbase.com/organization/cloudsufi>) — <what it says, 20 words max>

Where `<LABEL>` is exactly one of: `CONTRADICTED` | `PARTIAL` | `VERIFIED` | `NO DATA` (backticks are literal, part of the output).

DESCRIPTION (the text directly beneath the claim line) - always 2 to 3 sentences, written factually about the COMPANY, never about the interview or a person reporting on it:
- Sentence 1: state what public data shows on this topic.
- Sentence 2: add relevant detail, context, or variation across sources (e.g. that multiple independent sources agree, or how figures differ between them).
- Sentence 3 (optional): a notable nuance, a conflicting data point, or other additional context worth knowing.
- `NO DATA` claims still get 2 sentences: what was searched for, and that nothing public was found on it (no source lines follow).
- Never write "the interviewee said/stated/claimed/mentioned" or refer to "the candidate" or "the representative" - describe the claim itself and what public data shows about it, as if describing the company directly.

SOURCE LINES: include 2 "↳" lines per claim whenever the data supports it - zero for `NO DATA`, otherwise only drop down to 1 if a genuinely thorough check of targeted_search, blind_search, pages, github, and link_lookups together turns up just one distinct relevant source anywhere in the data. Do NOT stop at 1 just because the matching targeted_search entry only has one result - always also check blind_search/pages/github/link_lookups for a second distinct source before settling for 1. Every source line names its source clearly AND shows the actual URL path: `<Source name> (<url path>) — <description>`. The source name is the real publisher/platform name (e.g. LinkedIn, ZoomInfo, Growjo, RocketReach, Crunchbase, PR Newswire, Glassdoor, or the company's own brand name for its website) - never a generic descriptor like "Official website", "Official page", "LinkedIn Profile", or "ZoomInfo Profile". The URL path in parentheses is short and readable (e.g. `linkedin.com/company/cloudsufi`, `zoominfo.com/c/cloudsufi`, `growjo.com/company/CLOUDSUFI`), never a full https:// URL with long query strings. The description after the dash is up to 20 words and specific/informative, not generic. Use the matching targeted_search entry first, then anything directly relevant from blind_search/pages/github/link_lookups for the second source.

Ordering: group claims `CONTRADICTED` first, `PARTIAL` second, `VERIFIED` third, `NO DATA` last. Within each group, order financial claims first, then locations, then partnerships/certifications, then everything else.

If no claims survive the filtering, write exactly: "No claims required attention." and move directly to Additional Findings.

## 📌 Additional Findings
The most useful facts from blind_search, pages, github, or link_lookups that the Evaluator would not already know from the transcript and that are not already covered above - aim for 6 to 8 bullet points, each exactly one sentence, ending with the source as ([Source name](URL)). Only go below 6 if the data genuinely does not support that many distinct, non-generic findings - never pad with mission statements, company values, foundation/origin-story details, filler, or anything already covered in Interview Claims just to reach the count. Favor recent news, financials, reviews, leadership details, recent expansions, and reputation signals. If nothing qualifies at all, write exactly: "No additional findings."

STRICT OUTPUT RULES:
- No long paragraphs anywhere in the report - every line is a compact fragment or exactly one sentence, as shown in the block format above.
- Never repeat the same source (same URL/domain) across more than one claim or bullet - cite it once, in whichever place is most relevant.
- Strip marketing/promotional language from company-website sources; state facts plainly instead.
- Never use: candidate, interviewee, stated, claimed, said, mentioned, the representative (describe the company/claim itself, not the interview) - and no editorial or judgment words: wrong, false, lie, exaggerated, misleading.
- Do not use tech-assuming terms ("tech stack", "engineering team", "developers", "GitHub activity", "codebase") unless the data itself indicates a technical business - use "products or services", "team or workforce", "leadership or ownership" instead.
- The entire report must be readable in under 2 minutes - fit it in roughly one screen's scroll. Quality over quantity: when in doubt, cut a claim or bullet rather than keep it.

REMINDER: You are Scout, not the Evaluator. Present facts, mapped to their sources, using the status labels above. Never recommend, score, or draw a conclusion about whether to hire/select this company - that happens elsewhere, later, by a different agent, using a different process."""


settings = Settings()
