# Resonance — AI Vendor Evaluation Platform: Knowledge Transfer (KT)

This document has two parts:

- **Part 1 — Resonance: current status and roadmap.** What we've built so far, how it works in plain language, and what remains. This is the part to read for a status discussion.
- **Part 2 — Platform foundation deep dive.** The original LiveAvatar interview POC that Resonance is built on top of — architecture, deployment, testing. Read this to actually work on the code.

---

# Part 1: Resonance — Current Status & Roadmap

## 1.1 What is Resonance?

The project started as a POC where an AI avatar interviews a job candidate about their resume (that system still works and is documented in Part 2). It has now evolved into **Resonance**: a vendor-evaluation platform. A vendor representative fills in a short intake form, then has a live video conversation with an AI avatar that runs a structured evaluation interview — and behind the scenes a team of AI agents scores their answers in real time, researches the company, and recommends whether to book a follow-up meeting.

## 1.2 The one big architectural idea

In the original POC, HeyGen's cloud was the avatar's "brain" — we sent it a prompt and it handled the whole conversation. **In Resonance, our backend is the brain.** We use HeyGen's "Custom LLM" feature to point the avatar at our own server: every time the vendor finishes speaking, HeyGen calls *our* endpoint for the reply. That gives us full control — a deterministic interview flow, our own scoring, our own data — while HeyGen still handles the hard real-time parts (speech-to-text, lip-synced video, text-to-speech).

Two design rules worth mentioning to anyone reviewing this:

- **Code decides, the LLM phrases.** Which question comes next, when to branch, when to stop following up, how scores are averaged and weighted — all of that is deterministic code we can unit-test. Gemini is only asked to phrase the reply and judge the answer, always returning structured JSON.
- **The avatar never goes silent, and we never lose data.** Any failure in an agent turns into a polite canned reply, not an error. A summary, scoring, or follow-up failure never loses the transcript.

One environment variable (`PUBLIC_BASE_URL`) switches this mode on. Unset it and the app behaves exactly like the original POC — the legacy path is fully preserved and regression-tested.

## 1.3 The four agents

| Agent | What it does | Status |
|---|---|---|
| **Host** | Runs the interview: walks a configurable question tree (`data/questionnaire.yaml`), decides when an answer is complete vs. needs a follow-up, and branches based on what the vendor says (e.g. mentions AI/ML → go deeper on AI). | ✅ Done, verified in live sessions |
| **Appraiser** | Scores the interview 0–5 against a weighted rubric (`data/rubric.yaml`) in **one holistic pass at the end**, using a stronger Gemini Pro model, with supporting quotes per category. The scorecard is revealed in the results view after the session — deliberately not live, so the vendor never watches their own scores move mid-interview and every answer is judged in the context of the whole conversation. | ✅ Done |
| **Coordinator** | After the interview, applies transparent rules to the final scorecard (strong → advance, middling with weak spots → clarify) and drafts a ready-to-send follow-up meeting: title, agenda, duration, email draft — shown as a card in the UI. | ✅ Done |
| **Data Scout** | Researches the vendor in the background (uploaded docs + web search) and feeds intel into the Host's questions ("Your website mentions X — tell me more…"). | 🔄 In progress (Karan; work package in `docs/plans/2026-07-17-data-scout-agent-karan.md`) |

## 1.4 What has been done so far

Following the master plan (`docs/plans/2026-07-17-resonance-multi-agent-plan.md`):

1. **Phase 0 — Gateway spike ✅.** Proved with a real session that HeyGen will call our endpoint as its LLM, and documented the exact contract (request shape, streaming, its 10-second timeout) in `docs/llm-gateway-notes.md` before betting the plan on it.
2. **Phase 1 — Foundation + Host agent ✅.** Interview state store, questionnaire/rubric configs with validation, vendor intake form + endpoint, per-interview LLM registration with HeyGen (including a per-interview secret token so only HeyGen can call our gateway), and the Host state machine. The avatar greets the vendor by name and conducts a branching interview end-to-end.
3. **Phase 2 — Appraiser scoring ✅ (revised).** Originally scoring fired per answer with a live-updating scorecard panel. We deliberately changed this: scoring now happens **once, at the end of the interview**, as a single holistic pass over the whole transcript using a stronger model (Gemini Pro — latency doesn't matter after the session ends). Why: the vendor was seeing their own scores change mid-interview (bias risk), and judging the whole conversation at once is fairer than judging each answer in isolation. The scorecard (category bars, evidence quotes, overall) now appears in the results view alongside the summary.
4. **Phase 4 (partial) — Coordinator + enriched record ✅.** The saved interview record now carries the vendor profile, full transcript, scorecard, scout findings, summary, and follow-up proposal. The follow-up card (with copy-able email draft and `mailto:` link) appears when the rules recommend a meeting. *(Deployment and demo hardening from this phase are still open — see 1.5.)*
5. **First live test + hardening ✅.** The first end-to-end live run surfaced three issues, all diagnosed from logs and fixed:
   - *"Huge latency"* — Gemini was spending ~470 hidden reasoning tokens per turn. Fixed by lowering its reasoning effort and tightening our timeout to fit inside HeyGen's 10-second window. Server-side turn time dropped from ~3s to ~2s.
   - *Avatar kept asking "can you repeat that"* — Gemini returned malformed JSON on roughly half the turns, so the safety fallback kept firing. Fixed with strict schema-enforced output, a tolerant parser, and one fast retry.
   - *Scores not updating* — same root cause as above: because turns kept failing, no answer ever "completed", so scoring never triggered. Fixed by the same fix; verified advancing + scoring in the retest.
6. **Quality bar held throughout.** Every module has a matching test file; the suite grew from 221 to 237 tests, all green, and runs in CI on every push. Legacy (non-Resonance) mode is regression-tested and untouched.

## 1.5 What's next — the discussion list

In rough priority order:

1. **Deployment (E2, Nehil).** Gateway mode currently runs locally through a tunnel. To deploy: make the Docker image include the questionnaire/rubric YAML files, set `PUBLIC_BASE_URL` to the Cloud Run URL, and verify orphan cleanup also removes per-interview LLM configs/secrets on HeyGen's side. Docs update (this file) is part of it.
2. **Demo hardening (E3, Nehil).** Three full rehearsals with deliberately different vendor personalities (terse / rambling / off-topic) to tune prompts and follow-up limits; failure drills (kill the Gemini key mid-session — avatar must keep talking; state endpoint down — UI must cope); final latency check.
3. **Data Scout (Karan, in progress).** Background research agent + insights panel; once Karan has real findings from a live run, Nehil wires them into the Host's prompt (task D2).
4. **Optional latency polish.** If the demo still feels slow, stream the Host's reply token-by-token so the avatar starts speaking sooner. Deferred deliberately — the timing logs we added will tell us if it's needed.
5. **Post-POC (first items of a next phase, not this one).** Interview state is in-memory today — a server restart loses an in-flight interview. Accepted for the POC; a database is the first follow-up. Same for a small known session-counter drift in the legacy path.

## 1.6 Known limitations (worth stating upfront)

- **In-memory state:** an interview interrupted by a backend restart starts over. Fine for a POC/demo, not for production.
- **Latency:** ~2 seconds of our server time per turn, plus HeyGen's speech-to-text/text-to-speech on top (which we don't control). Acceptable in testing; E3 re-checks it.
- **Gateway mode needs a public URL:** locally that's a tunnel; in production it's the Cloud Run URL (part of E2).

---

# Part 2: Platform Foundation Deep Dive

Everything below describes the underlying platform the Resonance work builds on — the original interview POC. It still runs as-is ("legacy mode") when `PUBLIC_BASE_URL` is unset, and all of its plumbing (session security, resume parsing, transcripts, GCS persistence, deployment) is reused by Resonance.

## 1. The Big Picture: What Are We Building?

Imagine a candidate sitting down for a technical interview, but instead of a human engineer, they are greeted by an AI avatar. This avatar can see their resume, ask them questions about AI Engineering (like RAG or LLMs), and respond in real-time with perfectly lip-synced video and audio. 

**Our Goal:** To build a robust, scalable web application that conducts this interview using HeyGen's LiveAvatar technology, while ensuring the system is secure and cost-effective.

---

## 2. The Tech Stack (and Why We Chose It)

To make this happen seamlessly, we divided the application into two main parts: a fast, modern frontend for the user, and a secure backend to talk to the AI services.

### The Frontend (What the User Sees)
- **React 19 & TypeScript**: We chose React for its component-based architecture, making it easy to manage complex UI states (like whether the avatar is listening or speaking). TypeScript ensures we catch errors early.
- **Vite**: A lightning-fast build tool that replaces older tools like Webpack. It makes local development incredibly quick.
- **Tailwind CSS v4 & Lucide React**: Tailwind allows us to rapidly style the application without leaving our HTML, and Lucide provides clean, modern icons.
- **`@heygen/liveavatar-web-sdk`**: This is the secret sauce. It handles the extremely complex WebRTC (real-time communication) connection between the user's browser and HeyGen's video streaming servers. We are using **FULL Mode**, which means HeyGen's servers drive the conversation loop. In legacy mode HeyGen also supplies the LLM (AI brain); in Resonance gateway mode we register a "Custom LLM" so HeyGen calls *our* backend for every reply (see Part 1).

### The Backend (The Secure Middleman)
- **Python 3.13 & FastAPI**: Python is the standard for AI applications. FastAPI is incredibly fast and easy to use for building APIs. 
- **`uv`**: A modern, extremely fast Python package manager that replaces `pip` and `virtualenv`.
- **`pymupdf` & `python-docx`**: These libraries allow us to read the text inside PDFs and Word documents that the user uploads.
- **`httpx`**: An async HTTP client. We use it both to talk to the LiveAvatar API and to call Gemini's OpenAI-compatible chat endpoint when generating the interview summary.
- **`google-cloud-storage`**: Persists finalized interview transcripts + summaries to a GCS bucket (when `GCS_BUCKET` is configured; otherwise we fall back to local JSON files for development).
- **`pytest` (+ `pytest-asyncio`, `respx`, `pytest-cov`)**: The test stack. `respx` mocks outbound HTTP so tests never hit LiveAvatar or Gemini for real; GCS is faked in-memory. See section 8.

### Why do we even need a backend?
You might wonder, *"Why can't the React app just talk to LiveAvatar directly?"*
**Security.** To start a LiveAvatar session, you need an API key. If we put that key in the React code, anyone could view the source code in their browser, steal the key, and rack up a massive bill on our account. The backend acts as a **Proxy**. The React app asks the backend to start a session, the backend securely uses the API key to get a temporary `session_token`, and gives that token to React.

---

## 3. Step-by-Step Architecture Walkthrough

Let's follow the data flow when a user actually uses the app.

1. **Loading the App**: The user navigates to our Cloud Run URL. The FastAPI backend receives the request and, because it's configured to serve static files, it sends the compiled React app (`index.html`, JavaScript, CSS) to the browser.
2. **Uploading Context**: The candidate uploads their resume (up to 5 files). The React app sends these files to the backend via the `/api/upload-resume` endpoint.
3. **Processing Context**: The backend uses `pymupdf` or `python-docx` to extract the text. It combines this text with a base system prompt ("You are an AI interviewer..."). It then securely calls LiveAvatar's API (`POST /v1/contexts`) to save this specific candidate's context. LiveAvatar replies with a unique `context_id`.
4. **Starting the Session**: The React app now calls the backend's `/api/session` endpoint, passing along the `context_id`. 
5. **Token Generation**: The backend uses our secure `LIVEAVATAR_API_KEY` to ask LiveAvatar for a `session_token`. We hardcode `is_sandbox: True` here to save money during testing, and we use a specific Sandbox Avatar ID (`dd73ea75...`).
6. **WebRTC Connection**: The backend gives the `session_token` back to React. React passes it to the `LiveAvatarSession` SDK. The SDK reaches out to LiveKit (the underlying infrastructure) and establishes a direct video/audio peer-to-peer connection. The avatar appears on screen!
7. **Live Transcript**: During the interview, the SDK emits `USER_TRANSCRIPTION` (candidate) and `AVATAR_TRANSCRIPTION` (interviewer) events, one per completed turn. React accumulates these into a transcript, shown live in the transcript panel.
8. **Finalizing the Interview**: When the session ends — whether the candidate stops it or LiveAvatar's server ends it (e.g. max duration) — React POSTs the captured turns to `/api/transcript/finalize`. The backend asks Gemini to write a structured Markdown summary, then persists the full record (summary + all turns + timestamp) to Google Cloud Storage (or a local JSON file). The summary appears on screen, and the candidate can download the whole record as Markdown.

---

## 4. Deep Dive: Frontend Mechanics (`App.tsx`)

If you look inside `/liveAvatar/frontend/src/App.tsx`, here are the key concepts you need to understand:

- **Environment Variables & Fallbacks**: During deployment, we intentionally exclude the local `.env` file for security. To prevent the app from breaking when it can't find `import.meta.env.VITE_CONTEXT_ID`, we've added hardcoded fallback IDs in the code.
- **Event Listeners (VAD)**: Voice Activity Detection (VAD) is how the system knows who is talking. The `LiveAvatarSession` object emits events like `AgentEventsEnum.AVATAR_SPEAK_STARTED` and `USER_SPEAK_STARTED`. We listen to these events to change our React state (`speakingState`), which animates the little audio bars on the screen.
- **The "Before Unload" Safety Net**: LiveAvatar only allows a certain number of concurrent sessions. If a user just closes their browser tab, the session might stay alive for a few minutes on the server, blocking others. We use the browser's `beforeunload` event with `keepalive: true` to send a final `/api/session/stop` request as the tab dies, ensuring clean cleanup.
- **Transcript Capture & Finalization**: `useLiveAvatarSession` listens for the SDK's `USER_TRANSCRIPTION`/`AVATAR_TRANSCRIPTION` events and appends each completed turn to a `useRef` array (a ref, not just state, so the transcript survives the state reset that happens during cleanup). When the session ends, its `onSessionEnd` callback hands those turns to `useInterviewSummary`, which POSTs them to `/api/transcript/finalize` and drives the `SummaryPanel`. `utils/downloadTranscript.ts` turns the summary + turns into a downloadable Markdown file.

---

## 5. Deep Dive: Backend Mechanics (`app/`)

Inside `/liveAvatar/backend/app/` (routers + services, see `CLAUDE.md` for the module layout), the logic is designed for resilience:

- **Resonance modules (see Part 1 for the concepts):** `routers/vendor.py` (intake), `routers/interview.py` (live state endpoint the scorecard polls), `routers/llm_gateway.py` (the OpenAI-compatible endpoint HeyGen calls, with per-interview bearer-token auth and a "never return an error after auth" rule), and `services/interview_state.py`, `interview_config.py`, `host_agent.py`, `appraiser_agent.py`, `coordinator_agent.py`, `llm_json.py` (shared tolerant JSON parser for all Gemini calls). Question tree and rubric live in `data/questionnaire.yaml` / `data/rubric.yaml`.

- **Concurrency Tracking**: We maintain a simple `active_sessions_count` variable. This helps us monitor if we are hitting LiveKit's concurrency limits.
- **Garbage Collection (Contexts)**: When a session stops (in `/api/session/stop`), we don't just stop the video. We also send a `DELETE` request to LiveAvatar to destroy the `context_id` we created for that specific resume. If we didn't do this, our LiveAvatar workspace would eventually fill up with thousands of temporary resumes.
- **FastAPI Static Serving**: Notice the `fallback_to_index` middleware. This tells FastAPI: "If someone asks for a URL that isn't an API route (like `/`), send them the React `index.html`." This is how we achieve a Single Unified Container.
- **Transcript Persistence (`services/transcript_store.py`)**: Interview records are saved as `transcripts/{session_id}.json`. If `GCS_BUCKET` is set, they go to Google Cloud Storage (using Application Default Credentials); otherwise they're written to a local `transcripts/` folder — handy for local dev without any cloud setup. The blocking GCS/file calls run in a thread (`asyncio.to_thread`) so they don't stall the async event loop. This service intentionally has no `try/except`: if a save fails, the error bubbles up to the router, which returns a 500 rather than pretending the transcript was saved.
- **Summary Generation (`services/summary_service.py`)**: When an interview ends, we render the transcript into a readable "Interviewer:/Candidate:" script and send it to Gemini's OpenAI-compatible chat endpoint (via `httpx`) with a system prompt that asks for a structured Markdown summary (topics covered, strengths, gaps, notable answers, recap). This service raises on any failure — it's the router's job to decide what to do about it.
- **Soft-Fail Design (`routers/transcripts.py`)**: The `/api/transcript/finalize` route ties the two together. It tries to generate a summary, but if Gemini is unavailable or errors out, it logs a warning, sets `summary_ok=False`, and *still saves the transcript*. Losing a candidate's whole interview because a summary call hiccuped would be far worse than shipping the transcript without a summary. Saving the transcript itself, however, is not optional — if that fails, the route returns a 500.

---

## 6. Deployment Strategy (Google Cloud Run)

We deploy both the frontend and backend together as a single container on Google Cloud Run. This is much cheaper and simpler to manage than deploying them separately.

- **The Dockerfile (Multi-Stage)**:
  1. **Stage 1 (Node.js)**: It installs `npm` packages, builds the React app into optimized static files (inside `/dist`), and then discards all the heavy Node.js source files.
  2. **Stage 2 (Python)**: It starts fresh with a lightweight Python image. It installs FastAPI, copies the Python backend code, and finally copies just the compiled `/dist` folder from Stage 1. It then starts `uvicorn` on port 8080.
- **The `.gcloudignore` File**: This is critical. It tells Google Cloud "Do NOT upload my local `node_modules` or `.venv` folders to the cloud builder." If you upload a Mac's `.venv` folder to a Linux cloud builder, the build will fail because the binaries are incompatible.
- **Secret Manager**: The `deploy_setup.sh` script automates creating a secure vault (Google Cloud Secret Manager) for our `LIVEAVATAR_API_KEY` and giving Cloud Run permission to read it. When the container boots, Cloud Run injects the secret securely as an environment variable.

---

## 7. Troubleshooting & FAQ

**Q: The avatar connects but is completely silent and ignores speech. What's wrong?**
**A:** This is almost always a missing `context_id`. In FULL Mode, if LiveAvatar doesn't know *what* its personality is (the context), it goes into a restricted mode where it streams video but ignores input. Check that the `.env` variables or the fallback IDs in `App.tsx` are correctly being passed in the `/api/session` payload.

**Q: I'm getting an "Active session already exists" error.**
**A:** A previous session didn't close cleanly (e.g., your browser crashed, bypassing the `beforeunload` event). The system is protecting you from starting a second concurrent session. You can either wait 5 minutes for the idle timeout, or manually trigger the `/api/session/stop` endpoint.

**Q: Cloud Build is failing with an error about missing modules or incompatible binaries.**
**A:** You likely accidentally uploaded your local environments. Ensure `.gcloudignore` is present in the root of the project and contains `node_modules`, `.venv`, and `.env`.

**Q: Why is `is_sandbox: True` hardcoded?**
**A:** We are currently using the default Sandbox Avatar ID. If you try to use a sandbox avatar with `is_sandbox: False`, LiveKit will inexplicably time out and the connection will fail. When we move to production with a custom avatar, this flag must be changed.

**Q: The interview ended but the summary says it couldn't be generated. Was the transcript lost?**
**A:** No. Summary generation is best-effort — if Gemini is unavailable or `GEMINI_API_KEY` is missing, the backend sets `summary_ok=False` but still saves the full transcript, and the UI still lets you download it. Check the backend logs for the summary warning. If the transcript itself failed to save (a 500), that's a real error — check `GCS_BUCKET` / credentials, or the local `transcripts/` folder's write permissions.

**Q: Where do transcripts get stored?**
**A:** If `GCS_BUCKET` is set, into that bucket at `transcripts/{session_id}.json` (using Application Default Credentials). If it's unset, into a local `backend/transcripts/` folder — this is the zero-config dev fallback and is gitignored.

---

## 8. Testing

The backend has a `pytest` suite under `/liveAvatar/backend/tests/` with a test file for every module in `app/`.

- **Run it:** from `/liveAvatar/backend`, `uv sync` then `uv run pytest` (add `--cov` for a coverage report). Config lives in `pyproject.toml` (`asyncio_mode=auto`, branch coverage on `app`, the `dev` dependency group).
- **No real network or cloud:** outbound HTTP (LiveAvatar + Gemini) is mocked with `respx`, and Google Cloud Storage is replaced by hand-rolled in-memory fakes in `tests/fakes.py`. Tests never hit a live service or need credentials.
- **Shared fixtures (`tests/conftest.py`):** `patch_settings` for overriding config per-test, a FastAPI `TestClient` that deliberately skips the lifespan (so tests don't provision Gemini), `fake_gcs_client`, and `tmp_transcripts_dir` for the local-file transcript path.
- **CI:** `.github/workflows/ci.yml` runs this suite (plus an import-sanity check) on every push and pull request, alongside the frontend lint/build.