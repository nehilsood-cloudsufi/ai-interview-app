# Data Scout Agent — Work Package for Karan

> ## ⚠️ Status (2026-07-20): superseded — the Scout has been built, differently
>
> The 2026-07-20 meeting changed the Scout's design, and it has since been **implemented** on the MVP-1 branch (`app/services/scout_agent.py` + `tests/services/test_scout_agent.py`, orchestrated by `app/services/pipeline.py`). Do **not** build from this document. What changed:
>
> - **Post-interview only.** The Scout runs as step 1 of the background pipeline *after* the interview ends — never during it. Its findings go to the Evaluator and the results UI, **never into the Host's prompt** (the interview must stay unbiased). §1's diagram and Milestone C are obsolete.
> - **No document digestion.** Document upload was removed from the product entirely; the web-research half (Gemini native API + `google_search` grounding, exactly as §5 Milestone B describes) is the whole agent. Milestone A is obsolete.
> - **Entry point is `scout_agent.run(state)`** (not `run_initial_scout`), called by `pipeline.py`; it soft-fails to `[]` and honors `SCOUT_ENABLED`, as this doc specified. The `ScoutFinding` contract (§3) survived unchanged.
> - **No separate insights panel** (Milestone D): findings render in the post-interview summary view, which polls `GET /api/interview/{id}/state`.
>
> The patterns/gotchas sections (§4, §8) are still good onboarding reading. Current truth: `../KT.md`, root `CLAUDE.md`.

**Owner:** Karan (intern) · **Mentor/reviewer:** Nehil · **Target:** ~4–5 working days of effort, within the Week 2–3 window of the Resonance plan
**Parent plan:** `liveAvatar/docs/plans/2026-07-17-resonance-multi-agent-plan.md` (read the Context, Global Constraints, and Phase 3 sections before starting)

---

## 1. The project in two minutes

We are evolving the `liveAvatar/` POC (an AI avatar that interviews people over live video, built on HeyGen's LiveAvatar SDK) into **Resonance** — a vendor-evaluation platform. During a live interview, three AI agents cooperate:

- **Host** — the avatar's brain; drives a branching interview with the vendor. Runs inside our FastAPI backend.
- **Appraiser** — scores each answer against a rubric; feeds a live scorecard in the UI.
- **Data Scout — your agent.** While the interview is happening, it quietly researches the vendor in the background and surfaces what it finds.

```
Interview starts
   │
   ├── Host talks to the vendor (foreground, latency-sensitive)
   │
   └── DATA SCOUT (background, yours)
         ├─ researches the vendor on the public web (Gemini + Google Search grounding)
         ├─ digests any documents the vendor uploaded before the session
         └─ writes findings into the interview's shared state
                ├─→ the Host reads them and probes discrepancies in later questions
                └─→ the UI shows them to the evaluator in an "insights" panel
```

Your work is the full Data Scout slice: the backend agent, its tests, and the frontend insights panel. (The small change that injects your findings into the Host's prompt touches `host_agent.py`, which Nehil owns — you'll coordinate on that, see §5, Milestone C.)

## 2. What the Data Scout must do (requirements)

1. **Trigger:** when an interview session starts (not before, not on every turn), kick off enrichment in the background. It must never delay or block the conversation.
2. **Two research sources, run concurrently:**
   - **Web:** research the company using the `company_name` and `website` the vendor entered in the intake form. Aim for 3–6 findings across topics like company overview, recent news, credibility signals, and tech footprint — each with a source URL where available.
   - **Uploaded documents:** if the vendor uploaded docs at intake, their extracted text is already available on the interview state — distill findings from it too.
3. **Findings land in shared state** as they complete (don't wait for everything to finish before publishing anything).
4. **Failure is invisible:** any error (API down, quota, bad response) is logged and swallowed. A dead Scout must never break an interview. Partial success is fine — if web research fails but the doc digest works, publish the doc findings.
5. **Kill switch:** a `SCOUT_ENABLED` setting (config already planned) disables the Scout entirely — for demos where we can't risk external calls.
6. **Evaluator UI:** a panel in the frontend that shows findings grouped by topic, with source links, and a sensible "scouting…" state while results are pending.

**Out of scope for you:** internal CRM/DB integration (later phase), Scout re-runs mid-session, caching across sessions, any change to how the Host or Appraiser work.

## 3. Contracts you must honor (the parts that are NOT up to you)

Other people's code consumes your output, so these shapes are fixed — internals are yours to design:

- **Finding shape** (defined in `app/services/interview_state.py`, Task A1 of the parent plan):
  ```python
  @dataclass
  class ScoutFinding:
      topic: str            # e.g. "company_overview", "recent_news", "credibility", "tech_footprint", "uploaded_docs"
      summary: str          # 1-3 sentences, evaluator-readable
      source_url: str | None
  ```
- **Where findings go:** append to `InterviewState.scout_findings` (the shared per-interview state object). Never overwrite the list — the Host may already have read part of it.
- **Entry point:** an async function the session-start code can fire and forget (the parent plan calls it `run_initial_scout(state)` — keep that name so the wiring in `sessions.py` matches).
- **How the UI gets data:** findings are served by the existing (Task C2) `GET /api/interview/{interview_id}/state` endpoint — you should not need a new endpoint; your panel consumes the `insights` field from the same poll the scorecard uses.

**Dependencies:** your backend work needs Task A1 (state store) and A2 (config) merged first; your UI panel needs Task C2/C3's poll hook. If you start before those land, build against the interfaces above and rebase — or start with the doc-digest path, which only needs A1.

## 4. Before writing any code — study these

1. `CLAUDE.md` (repo root) and `liveAvatar/docs/KT.md` — how this codebase is organized and its known gotchas.
2. The parent plan's **Phase 3** and **Global Constraints** sections.
3. **Pattern to copy for LLM calls:** `liveAvatar/backend/app/services/summary_service.py` — how we call Gemini with raw `httpx`, no SDK, and how errors are deliberately raised vs. soft-failed at the caller. Your Scout inverts this: it soft-fails internally.
4. **Pattern to copy for background work:** how `transcript_store.py` uses `asyncio.to_thread` and how the finalize router fires-and-forgets vs awaits.
5. **Pattern to copy for tests:** `liveAvatar/backend/tests/services/test_summary_service.py` — respx-mocked HTTP, no real network calls in tests, ever.
6. **Pattern to copy for the UI:** `liveAvatar/frontend/src/hooks/useConcurrencyPoll.ts` (polling) and any existing panel component (e.g. `SummaryPanel.tsx`) for structure/styling conventions.

## 5. Suggested milestones (each ends in a PR)

### Milestone A — Doc-digest Scout (backend, ~1 day)
The simpler half: given `state.vendor_profile.doc_text`, produce `ScoutFinding`s tagged `topic="uploaded_docs"` via a Gemini call. Full test coverage with mocked HTTP. This gets you through the whole toolchain (uv, pytest, respx, our service style) on the easiest path.

### Milestone B — Web research via Google Search grounding (backend, ~1.5–2 days)
The interesting half. Key technical fact: **Gemini's Google-Search grounding is NOT available on the OpenAI-compatible endpoint** we use elsewhere. You'll use Gemini's native REST API (`generativelanguage.googleapis.com`, `generateContent` with `tools: [{"google_search": {}}]`) and parse the grounding metadata for source URLs. Read Google's grounding docs first and do a quick throwaway script against the real API (your own experiment, not committed) to see a real response shape before mocking it in tests.
Then combine A+B: both branches run concurrently, failures isolated per branch (hint: `asyncio.gather(..., return_exceptions=True)`), findings appended as each branch completes, everything behind the `SCOUT_ENABLED` switch.

### Milestone C — Wiring + Host handoff (~0.5 day)
Add the fire-and-forget trigger where the session becomes active (in `sessions.py`; small change — pair with Nehil on the exact spot). Then hand over: once your findings are in state, **Nehil wires the Host prompt injection** — give him 2–3 realistic example findings from a real run so he can tune the prompt.

### Milestone D — Insights panel (frontend, ~1 day)
`ScoutInsightsPanel` component: findings grouped by topic, source links open in a new tab, loading/skeleton state, empty state ("No public information found") — data from the existing interview-state poll. `npm run lint` and `npm run build` must pass.

## 6. Definition of done

- [ ] All new backend code lives in `app/services/` with matching tests in `tests/services/`; `uv run pytest -q` green; no real network calls in tests.
- [ ] An interview with Scout enabled shows findings in the UI within ~30s of session start (demo this to Nehil on a live session).
- [ ] An interview with a bogus company name / dead website still completes normally (Scout logs, interview unaffected).
- [ ] `SCOUT_ENABLED=false` → zero outbound Scout calls (prove with a test).
- [ ] The Gemini API key is only ever read from settings — never hardcoded, never logged, never sent to the frontend.
- [ ] Frontend lint + build clean.
- [ ] Short section added to `docs/KT.md`: what the Scout does, its config, and its failure behavior.

## 7. Working agreements

- Branch per milestone: `feature/karan/data-scout-<milestone>`; small commits; PR to `main` with a description of what/why and how you tested it. CI must be green before review.
- **Write the test first** when you can (the codebase is TDD-friendly — every module has a 1:1 test file).
- Stuck for more than ~45 minutes on environment/tooling, or more than ~2 hours on a design question → ask. Include what you tried. Asking early is a strength here, not a weakness.
- Don't refactor code outside your slice, even if it looks tempting — note it and mention it in the PR instead.

## 8. Gotchas that will bite you if you skip this section

- **Never `await` your research inside the session-start request path** — that's what fire-and-forget (`asyncio.create_task`) is for. If session creation gets slower because of the Scout, something is wrong.
- **In-memory state means restarts wipe interviews.** If your findings vanish while testing, check whether the backend reloaded (`--reload` restarts on file save).
- **Grounded responses vary wildly.** Some companies return rich results, unknown ones return almost nothing. Design prompts and parsing for "possibly empty" from day one; the empty state is a first-class outcome, not an error.
- **respx mocks match by URL** — your tests for the native Gemini endpoint and the OpenAI-compatible one (used by other services) are different hosts/paths; don't copy a mock and forget to change the route.
- **Local dev doesn't need the LLM-gateway tunnel.** Your agent is triggered server-side and testable via unit tests plus a tiny script that builds a fake `InterviewState` and calls `run_initial_scout` directly — you don't need HeyGen at all until the final live demo.

Good luck — this is the most self-contained and most demo-visible of the three agents. Have fun with it.
