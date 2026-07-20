# Resonance Multi-Agent Interview — The Plan, Explained

**What this document is:** the implementation plan for turning our `liveAvatar/` POC into the Resonance vendor-evaluation experience, written to be read and understood — not just executed. Every phase explains *what* we're building, *why* it's built that way, and *how we'll know it works*.

**Companion docs:**
- `2026-07-17-data-scout-agent-karan.md` — Karan's self-contained work package for the Data Scout agent.
- `liveAvatar/docs/KT.md` — how the current codebase works and its known gotchas.
- The design deck: `liveAvatar/docs/Design session 2_ SAIL - Resonance.pptx`.

---

## 1. Where we are and where we're going

**Today:** the app is a single-flow interview POC. A candidate uploads a resume, a HeyGen avatar interviews them, and at the end we save a transcript plus an AI-generated summary. Crucially, the *conversation brain* runs entirely on HeyGen's servers — we provision our Gemini key into their platform and they do the talking. Our backend is just a secure proxy.

**The vision (from the Design Session 2 deck):** Resonance — a vendor-evaluation platform where five AI agents collaborate inside one live session: Concierge, **Host**, **Data Scout**, **Appraiser**, and Coordinator, plus deterministic scoring, live scorecards, dashboards, and admin tooling.

**This phase builds four of those things (three core agents agreed on 2026-07-17, plus a minimal Coordinator added on review):**

| Agent | What it does in one line |
|---|---|
| **Host** | The avatar's brain — greets the vendor, verbally verifies their details, and drives a *branching* interview where questions adapt to answers |
| **Appraiser** | Scores each completed answer against a weighted rubric, feeding a scorecard that fills in live during the session |
| **Data Scout** | Researches the vendor in the background (public web + their uploaded docs) and feeds findings to both the evaluator and the Host |
| **Coordinator** | When the final evaluation flags it, recommends a follow-up meeting and drafts the invite (agenda + email) — the evaluator sends it, we don't touch their calendar |

**Explicitly out of scope this phase:** Concierge, login/auth/RBAC, dashboards, admin UI. The Coordinator stays minimal: it *recommends and drafts* — actual calendar/email API integration (auto-booking the meeting) is a later phase. Rubrics and question sets live in config files for now, not an admin screen. No database — we extend the existing GCS/local-JSON persistence. We have a **demo in ~2–3 weeks**, so every phase ends in something showable.

## 2. The one big architectural idea

Everything in this plan hangs on a single decision: **our backend becomes the LLM.**

LiveAvatar's "Custom LLM" feature lets you point a session at any OpenAI-compatible endpoint. Today we point it at Google's Gemini servers. Instead, we'll point it at *ourselves* — a new endpoint in our own FastAPI backend. HeyGen keeps doing everything hard about real-time video (speech recognition, text-to-speech, lip-sync, WebRTC), but every conversational turn now flows through our code:

```
Vendor speaks
   → HeyGen transcribes it (ASR)
   → HeyGen calls OUR endpoint: POST /llm/{interview_id}/v1/chat/completions
        ┌────────── our FastAPI backend ──────────────┐
        │  HOST agent: looks at the question tree,    │
        │  decides what to say next (asks Gemini to   │
        │  phrase it), and decides whether the answer │
        │  is complete                                 │
        │     ├─ answer complete? → fire APPRAISER    │
        │     │   in the background to score it        │
        │     └─ Scout findings ready? → weave them   │
        │         into the Host's context              │
        └──────────────────────────────────────────────┘
   → our reply streams back to HeyGen
   → HeyGen speaks it through the avatar
```

**Why this way and not the alternatives we considered:**
- *Keep HeyGen↔Gemini as-is and bolt agents on the side* — cheapest, but the Host would just be a system prompt. No deterministic branching, no way to inject Scout findings into the conversation, and the "every vendor is evaluated the same way" story from the deck falls apart.
- *LITE Mode (build our own speech pipeline)* — maximum control, but then we own speech recognition, turn-taking, audio formats, and latency. Massive over-engineering for a 3-week window.
- *Backend-as-LLM (chosen)* — full control over the conversation for moderate effort, and we keep HeyGen's polished voice/video. The catch: it rests on assumptions about how HeyGen calls custom LLM endpoints — which is exactly why Phase 0 exists.

**One operational consequence to internalize:** HeyGen's servers must be able to *reach* our backend over the public internet. Fine on Cloud Run; for local development you need a tunnel (e.g. `cloudflared`) — or you use **legacy mode**: when the new `PUBLIC_BASE_URL` setting is unset, sessions fall back to exactly today's behavior. That switch keeps local dev painless and gives us a safety net.

## 3. Decisions already made (and why)

These were settled with stakeholder input on 2026-07-17 — don't relitigate them mid-build:

- **Question tree in a config file, LLM only phrases** — the interview's structure (questions, branch conditions, which rubric categories each question feeds) lives in a versioned YAML file. Code walks the tree deterministically; Gemini just makes it sound natural and handles follow-ups. This is what makes scoring auditable and consistent.
- **Scores reach the UI by polling** — the frontend polls one endpoint every few seconds, same pattern as the existing concurrency badge. No WebSockets/SSE this phase; simplest thing that shows a "live" scorecard.
- **Deterministic scoring, literally** — the LLM judges *one answer at a time* and returns per-category scores; pure Python computes all aggregation (weighted averages, the overall number). The math is testable to exact values.
- **No database** — per-interview state lives in memory during the session; at finalize, one enriched JSON record (profile + transcript + scorecard + findings + summary) goes through the existing `transcript_store` (GCS or local). A Cloud Run restart loses in-flight interviews; acceptable for a POC, noted for later.
- **Scout uses Gemini's Google-Search grounding** — we already have the Gemini key; no new vendor to onboard. Scout findings *do* get injected into the Host's context (that's the visible multi-agent "wow" in the demo).
- **Vendor domain now** — intake is a small form (company name, website, contact) plus optional document upload reusing the existing parser. "Identity verification" this phase = the Host verbally confirming those details at the start.
- **Plain asyncio, no agent framework** — three cooperating services don't need LangGraph or ADK. Each agent is a Python module in `app/services/`, matching the existing codebase style.

## 4. Global constraints (read before writing any code)

- Keep `is_sandbox: True` with the sandbox avatar (`dd73ea75-…`) — mixing sandbox avatar with production flag hangs the session (see `KT.md`).
- FULL Mode **always** needs a `context_id`, even with a custom LLM — a session without one streams video but stays silent, with no error. We keep a minimal static context purely to avoid this trap.
- Secrets stay server-side: the LiveAvatar API key as always, and the new per-interview gateway token is also backend-minted. Nothing sensitive reaches the browser.
- **The avatar must never go silent because of our bugs.** Any exception in the gateway path returns a canned recovery line ("Let me rephrase that…"), never a 500.
- A scoring or summary failure must never lose the transcript (existing soft-fail semantics stay).
- Test parity is non-negotiable: every new `app/` module gets a matching `tests/` module; all HTTP mocked with respx; CI stays green. Python 3.13 + `uv`; frontend `oxlint` clean.
- Don't overcomplicate: no frameworks, no DB, no WebSockets this phase.

**New configuration** (all in `app/config.py`): `PUBLIC_BASE_URL` (the legacy-mode switch, see §2), `QUESTIONNAIRE_PATH` / `RUBRIC_PATH` (default to `data/*.yaml`), `SCOUT_ENABLED` (demo kill switch).

## 5. The phases

The order is dictated by risk and dependencies: prove the risky assumption first, then build the conversation, then score it, then enrich it, then polish. Each phase ends at a demo checkpoint.

---

### Phase 0 — The Gateway Spike (~2 days, Week 1) — Nehil

**The question this phase answers:** *will HeyGen actually talk to our endpoint the way we assume?* Every later phase builds on that assumption, so we spend two days proving it before betting the plan on it.

What happens: stand up a throwaway endpoint that logs everything HeyGen sends and replies with a hardcoded line; expose it through a tunnel; register it as a custom LLM via LiveAvatar's API; run a real session and talk to the avatar.

**The deliverable is knowledge, not code:** a short `docs/llm-gateway-notes.md` recording the exact request shape (does HeyGen send full conversation history? where does the system prompt go?), whether streaming is mandatory, real latency numbers, and what happens on timeouts/errors. The spike code itself gets thrown away.

**Decision gate:** if something is fundamentally broken (say, HeyGen refuses arbitrary base URLs), we stop and re-plan around the fallback (prompt-only Host + agents fed by frontend transcript events) — having lost two days instead of two weeks.

---

### Phase 1 — Foundation + Host Agent (Week 1 → early Week 2)

**Demo checkpoint:** a vendor fills in a form, and the avatar greets them by name, confirms their company details, and conducts a branching interview end-to-end. No scoring yet — but the conversation is *ours*.

**Foundation first (intern-friendly, can start immediately — none of it depends on the spike):**

- **A1 — Interview state store** (`app/services/interview_state.py`): the shared memory all three agents read and write. One dataclass per interview (who's being interviewed, where we are in the question tree, the turns so far, scores, scout findings, the auth token for the gateway) plus a small in-memory store with create/get/prune. Modeled on the existing `session_state.py`. This file defines the shapes everyone else imports — it lands first.
- **A2 — Questionnaire + rubric configs** (`data/questionnaire.yaml`, `data/rubric.yaml`, loader in `app/services/interview_config.py`): the interview's DNA. Around 6–8 vendor questions with branch signals (e.g. "mentions AI/ML → go deeper on AI") and a rubric of ~4 weighted categories (weights must sum to 1.0 — the loader validates this, plus that every branch target exists). Authoring good questions matters more than the loader code.
- **A3 — Vendor intake endpoint** (`POST /api/vendor-profile`): accepts the form fields + optional docs, reuses the existing `resume_parser`, creates the interview state, returns an `interview_id`.

**Then the Host itself (Nehil — architecture-critical):**

- **A4 — Per-interview LLM registration**: when a session starts *with* an `interview_id` and `PUBLIC_BASE_URL` set, the backend registers a secret + LLM configuration with LiveAvatar pointing at `…/llm/{interview_id}/v1`, creates a minimal context, and mints the session token with all of it. On stop, it cleans all three resources up. Without those conditions → legacy path, untouched.
- **B1 — Host agent core** (`app/services/host_agent.py`): the state machine. Each turn, it builds one Gemini call containing the current question node, recent turns, the vendor profile, and any Scout intel, and asks for a structured JSON answer: *what to say next*, *is the current answer complete*, and *which branch signal fired*. Then **code** — not the LLM — advances the tree: resolve the branch (unknown signal → default), reset or increment the follow-up counter, force-advance if the vendor rambles past `max_followups`. First node is always `verify_identity`; last node produces the closing.
- **B2 — The gateway route** (`app/routers/llm_gateway.py`): the OpenAI-compatible endpoint HeyGen calls. Validates the per-interview bearer token, extracts the user's latest message, calls the Host, streams the reply back in exactly the format the Phase-0 notes documented. This is also where background agents get fired later — and where the "never go silent" rule is enforced.
- **B3 — Frontend intake** (intern-friendly): `VendorIntakeForm` component (company*, website, contact*, role, optional docs), wire the returned `interview_id` into session start, relabel the app from candidate-speak to vendor-speak.

---

### Phase 2 — Appraiser + Live Scorecard (Week 2)

**Demo checkpoint:** while the vendor talks, category scores visibly fill in on a scorecard panel within ~5 seconds of each completed answer. This is the headline demo moment.

- **C1 — Appraiser agent** (`app/services/appraiser_agent.py`): two cleanly separated halves. The *probabilistic* half sends one completed answer + the relevant rubric categories to Gemini and gets back per-category 0–5 scores with a supporting quote and rationale (code clamps ranges and drops anything unexpected). The *deterministic* half, `compute_scorecard`, is a pure function — per-category means, weighted overall, categories with no data excluded and remaining weights renormalized — testable to exact values with zero mocking. That pure function is a perfect standalone intern sub-ticket.
- **C2 — State endpoint + the hook** (`GET /api/interview/{id}/state`): one endpoint serving everything the UI needs (status, current topic, scorecard, scout insights). The gateway hook: when the Host marks an answer complete, fire the Appraiser as an `asyncio` background task wrapped in try/except-log — a failed score shows as "pending", never touches the conversation.
- **C3 — Scorecard panel** (intern-friendly): poll hook copied from `useConcurrencyPoll` (~4s while active), category rows with score bars and expandable evidence quotes, an overall headline number, "pending" states.

---

### Phase 3 — Data Scout (Week 2–3) — **Karan, see his dedicated doc**

**Demo checkpoint:** within ~30 seconds of session start the insights panel fills with real research about the vendor, and at least one later Host question visibly references it ("Your website mentions X — tell me more…").

The full slice — background agent (docs digest + web research via Gemini's *native* grounding API, since grounding isn't exposed on the OpenAI-compatible endpoint), failure-invisible design, `SCOUT_ENABLED` kill switch, and the insights panel — is specified as an outcome-oriented work package in `2026-07-17-data-scout-agent-karan.md`, with milestones, contracts, and gotchas.

**The one piece staying with Nehil (D2):** injecting Scout findings into the Host's prompt, because it touches `host_agent.py` and needs prompt tuning. Handoff: Karan supplies 2–3 real findings from a live run; Nehil wires and tunes the "Known vendor intel" block (present → rendered as bullets with an instruction to probe discrepancies; absent → omitted entirely).

---

### Phase 4 — Coordinator, Finalize, Deploy, Demo-Harden (Week 3)

**Demo checkpoint:** the real demo, rehearsed — including the closing beat: the interview ends and the app itself says *"this vendor is worth a second meeting — here's the invite, ready to send."*

- **E1 — Enriched final record** (intern-friendly): finalize gains the `interview_id`; the saved record now carries vendor profile + transcript + scorecard + scout findings + summary. `transcript_store` doesn't change (it saves a dict); the downloadable Markdown gains Scorecard and Insights sections. Summary failure still never loses the transcript. Without an `interview_id`, the legacy record shape is preserved.
- **F1 — Coordinator agent** (intern-friendly, `app/services/coordinator_agent.py`): the deck's "automatically offers or suggests a follow-up meeting when the evaluation flags one," built the same two-halves way as the Appraiser. The *deterministic* half, `evaluate_followup`, is a pure function over the final scorecard — no LLM: a strong overall score (≥ 3.5) recommends an **advance** (next-round deep-dive); a middling overall with at least one weak category recommends a **clarify** meeting focused on exactly those categories; a clear pass or clear reject recommends nothing. Rules and thresholds live in code, testable to exact values, so *why* a meeting was suggested is always explainable. The *probabilistic* half, `draft_followup`, takes that recommendation plus the evidence quotes and scout findings and asks Gemini for a ready-to-send package: meeting title, agenda bullets, suggested duration, email draft. If drafting fails, a plain template fallback is used — a drafting failure never suppresses the recommendation itself.
- **F2 — Follow-up in the record + UI** (intern-friendly, after E1 + F1): finalize runs the Coordinator after the summary (same soft-fail wrapping — a Coordinator crash never blocks saving the transcript), and the saved record plus the finalize response gain a `followup` field. In the UI, a `FollowupPanel` card appears *only* when a meeting was recommended: the headline and reason, the agenda, and two actions — **Copy email draft** and a prefilled `mailto:` link to the vendor contact. The Coordinator proposes; the human sends.
- **E2 — Deployment** (Nehil): Dockerfile must copy `data/*.yaml`; Cloud Run gets `PUBLIC_BASE_URL` set to its own service URL (this is what makes gateway mode work in production with no tunnel); update `KT.md` and `CLAUDE.md` for the new architecture; verify orphan-cleanup also removes per-interview LLM configs/secrets.
- **E3 — Demo hardening** (Nehil): three full rehearsals with deliberately different vendor personalities (terse / rambling / off-topic) to tune prompts and follow-up limits; latency check (user stops speaking → avatar responds in ~<3s, or we trim prompt history); failure drills — kill the Gemini key mid-session (avatar keeps talking), Scout disabled, UI handles a 404ing state endpoint.

## 6. Who does what, and in what order

**JIRA epics:**

| Epic | Tickets | Owner |
|---|---|---|
| Phase 0 — Gateway Spike | 0.1 | Nehil |
| Foundation | A1, A2, A3 (intern) · A4 (Nehil) | mixed |
| Host Agent | B1, B2 (Nehil) · B3 (intern) | mixed |
| Appraiser Agent | C1 (pair; `compute_scorecard` sub-ticket intern) · C2, C3 (intern) | mixed |
| Data Scout Agent | end-to-end **Karan** (his doc) · D2 stays with Nehil | Karan |
| Coordinator Agent | F1, F2 (intern) | intern |
| Finalize & Demo | E1 (intern) · E2, E3 (Nehil) | mixed |

**The dependency chain, in words:** the spike (0.1) unblocks LLM registration (A4), which unblocks the gateway (B2), which unblocks the Appraiser hook (C2) and Scout injection (D2). The state store (A1) is the root everything imports — it lands first. Configs (A2) feed the Host (B1). The Coordinator hangs off the Appraiser: F1's rule half only needs C1's `Scorecard` shape (so it can start as soon as C1 lands), and F2 needs both F1 and the enriched finalize (E1). Meanwhile A1/A2/A3, B3, C3, and Karan's early milestones **don't depend on the spike at all** — interns can start day one.

**Estimates** (ideal days): Phase 0 ≈ 2 · Phase 1 ≈ 6.5 · Phase 2 ≈ 4 · Phase 3 ≈ 4–5 (parallel, Karan) · Phase 4 ≈ 5 (of which the Coordinator's 2 are intern-parallel to Nehil's deploy/hardening work). With the parallel tracks this still fits the 2–3 week window with margin for the unknowns Phase 0 surfaces.

## 7. How we'll know it all works

1. **Unit level:** `cd liveAvatar/backend && uv run pytest -q` green with coverage held; `npm run lint && npm run build` clean.
2. **Legacy mode regression:** unset `PUBLIC_BASE_URL`, run a plain session — the app behaves exactly like today.
3. **The real test (gateway mode):** with `PUBLIC_BASE_URL` set — intake form → avatar verifies identity → questions branch on answers → scorecard fills per answer → insights appear → a Host question references scouted intel → stop → when the score warrants it, the follow-up card appears with agenda and email draft → the saved record contains everything (including `followup`) → LiveAvatar dashboard shows no orphaned sessions, configs, or secrets.
4. **Demo readiness:** the Phase-4 rehearsal checklist passes three times consecutively.

## 8. Risks, with eyes open

- **HeyGen's custom-LLM contract differs from our assumptions** — the whole point of Phase 0; worst case costs 2 days and we fall back to prompt-only Host + frontend-fed agents (agents survive; branching becomes prompt-guided rather than deterministic).
- **Turn latency** (HeyGen → Cloud Run → Gemini → back) — mitigations: `gemini-3.5-flash` (already our default), aggressive streaming, trimming the history window. Measured in the spike, re-checked in E3.
- **In-memory state dies with the container** — an interview interrupted by a Cloud Run restart restarts from scratch. Accepted for the POC; a database is the first item of the *next* phase.
- **Scout dependencies (search grounding quota/availability)** — `SCOUT_ENABLED=false` keeps any demo safe.
- **Two people + an intern on parallel tracks** — the interface contracts in A1 (state shapes) and C2 (state endpoint) are the coordination points; they're deliberately small and land early.
