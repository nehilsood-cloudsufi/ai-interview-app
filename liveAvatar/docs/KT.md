# Resonance — AI Vendor Evaluation Platform: Knowledge Transfer (KT)

This document has two parts:

- **Part 1 — Resonance: current status and roadmap.** What we've built so far, how it works in plain language, and what remains. This is the part to read for a status discussion.
- **Part 2 — Platform foundation deep dive.** The original LiveAvatar interview POC that Resonance is built on top of — architecture, deployment, testing. Read this to actually work on the code.

---

# Part 1: Resonance — Current Status & Roadmap

## 1.1 What is Resonance?

The project started as a POC where an AI avatar interviews a job candidate about their resume (that system's plumbing — sessions, transcripts, GCS persistence, deployment — is documented in Part 2 and is fully reused). It has now evolved into **Resonance**: a vendor-evaluation platform. A vendor representative starts an interview straight from a start screen (no intake form), then has a live video conversation with an AI avatar that runs a structured, unbiased evaluation interview — and once the interview ends, a team of background AI agents scores the transcript, researches the company, and recommends whether to book a follow-up meeting.

## 1.2 The one big architectural idea

In the original POC, HeyGen's cloud was the avatar's "brain" — we sent it a prompt and it handled the whole conversation. **In Resonance, our backend is the brain.** We use HeyGen's "Custom LLM" feature to point the avatar at our own server: every time the vendor finishes speaking, HeyGen calls *our* endpoint for the reply. That gives us full control — a deterministic interview flow, our own scoring, our own data — while HeyGen still handles the hard real-time parts (speech-to-text, lip-synced video, text-to-speech).

Two design rules worth mentioning to anyone reviewing this:

- **Code decides, the LLM phrases.** Which question comes next, when to stop following up, how scores are averaged and weighted — all of that is deterministic code we can unit-test. Gemini is only asked to phrase the reply and judge the answer, always returning structured JSON.
- **The avatar never goes silent, and we never lose data.** Any failure in an agent turns into a polite canned reply, not an error. A summary, scoring, or follow-up failure never loses the transcript.

`PUBLIC_BASE_URL` is required and must resolve to a URL HeyGen can reach — it's how HeyGen calls back into our `/llm/{interview_id}/v1` gateway. Gateway mode is now the only mode; the legacy path (HeyGen as its own brain, intake form, per-answer live scoring) has been fully removed, not just made optional.

## 1.3 The four agents

| Agent | What it does | Status |
|---|---|---|
| **Host** | Runs the live interview: walks a fixed linear question script, one complete script per domain (`data/questionnaires/{domain}.yaml`), and decides when an answer is complete vs. needs a follow-up — deliberately no adaptive branching, so every vendor gets the same questions. Also captures the vendor's profile (name, role, company, website) conversationally at the start — there's no intake form. | ✅ Done, verified in live sessions |
| **Evaluator** | Scores the interview 0–5 against a weighted rubric (`data/rubric.yaml`) in **one holistic pass after the interview ends**, using a stronger Gemini Pro model, with supporting quotes per category. The scorecard is revealed in the results view — deliberately not live, so the vendor never watches their own scores move mid-interview and every answer is judged in the context of the whole conversation. | ✅ Done |
| **Coordinator** | After the interview, applies transparent, deterministic threshold rules (no LLM) to the final scorecard (strong → advance, middling with weak spots → clarify) to produce a recommendation for the human evaluator to act on — shown as a card in the UI. | ✅ Done |
| **Data Scout** | Researches the vendor company on the web (Gemini native API + Google Search grounding) **strictly after the interview ends**, as a pipeline step — it never sees the interview and never informs the Host's questions, by design: the interview must stay unbiased. Soft-fails to no findings on any error. | ✅ Done |

## 1.4 What has been done so far

Following the master plan (`docs/plans/2026-07-17-resonance-multi-agent-plan.md`):

1. **Phase 0 — Gateway spike ✅.** Proved with a real session that HeyGen will call our endpoint as its LLM, and documented the exact contract (request shape, streaming, its 10-second timeout) in `docs/llm-gateway-notes.md` before betting the plan on it.
2. **Phase 1 — Foundation + Host agent ✅.** Interview state store, questionnaire/rubric configs with validation, `POST /api/interview` to mint an interview before any UI is shown, per-interview LLM registration with HeyGen (including a per-interview secret token so only HeyGen can call our gateway), and the Host state machine. The avatar greets the vendor conversationally and captures their profile (name, role, company, website) as part of the interview itself — there's no separate intake form.
3. **Phase 2 — Evaluator scoring ✅ (revised).** Originally scoring fired per answer with a live-updating scorecard panel. We deliberately changed this: scoring now happens **once, after the interview ends**, as a single holistic pass over the whole transcript using a stronger model (Gemini Pro — latency doesn't matter after the session ends). Why: the vendor was seeing their own scores change mid-interview (bias risk), and judging the whole conversation at once is fairer than judging each answer in isolation. The scorecard (category bars, evidence quotes, overall) now appears in the results view alongside the summary.
4. **Phase 3 — Data Scout ✅.** Post-interview-only company research (Gemini native API + `google_search` grounding), deliberately wired to run strictly after the interview so it can never bias the Host's questions. Soft-fails to no findings on any error.
5. **Phase 4 — Coordinator + pipeline + enriched record ✅.** `app/services/pipeline.py` sequences Scout → Evaluator → Coordinator as an in-process background task after finalize, tracking `pipeline_status` (`interviewed → scouting → evaluating → ready`/`failed`) that the frontend polls via `GET /api/interview/{id}/state`. The saved interview record carries the vendor profile, full transcript, scorecard, scout findings, summary, and follow-up recommendation. A plain recommendation card (advance/clarify, reason, weak categories — no email drafting, that was removed with the Coordinator simplification) appears when the rules recommend a next step. *(Deployment and demo hardening are still open — see 1.5.)*
6. **First live test + hardening ✅.** The first end-to-end live run surfaced three issues, all diagnosed from logs and fixed:
   - *"Huge latency"* — Gemini was spending ~470 hidden reasoning tokens per turn. Fixed by lowering its reasoning effort and tightening our timeout to fit inside HeyGen's 10-second window. Server-side turn time dropped from ~3s to ~2s.
   - *Avatar kept asking "can you repeat that"* — Gemini returned malformed JSON on roughly half the turns, so the safety fallback kept firing. Fixed with strict schema-enforced output, a tolerant parser, and one fast retry.
   - *Scores not updating* — same root cause as above: because turns kept failing, no answer ever "completed", so scoring never triggered. Fixed by the same fix; verified advancing + scoring in the retest.
7. **Quality bar held throughout.** Every module has a matching test file, all green, and the suite runs in CI on every push.

## 1.5 What's next — the discussion list

In rough priority order:

1. **Deployment (E2, Nehil).** Gateway mode currently runs locally through a tunnel. To deploy: make the Docker image include the questionnaire/rubric YAML files, set `PUBLIC_BASE_URL` to the Cloud Run URL, and verify orphan cleanup also removes per-interview LLM configs/secrets on HeyGen's side. Docs update (this file) is part of it.
2. **Demo hardening (E3, Nehil).** Three full rehearsals with deliberately different vendor personalities (terse / rambling / off-topic) to tune prompts and follow-up limits; failure drills (kill the Gemini key mid-session — avatar must keep talking; state endpoint down — UI must cope); final latency check.
3. **Optional latency polish.** If the demo still feels slow, stream the Host's reply token-by-token so the avatar starts speaking sooner. Deferred deliberately — the timing logs we added will tell us if it's needed.
4. **Post-POC (first items of a next phase, not this one).** Interview state is in-memory today — a server restart loses an in-flight interview. Accepted for the POC; a database is the first follow-up. Same for a small known session-counter drift (see `session_state.py` in `CLAUDE.md`).

## 1.6 Known limitations (worth stating upfront)

- **In-memory state:** an interview interrupted by a backend restart starts over. Fine for a POC/demo, not for production.
- **Latency:** ~2 seconds of our server time per turn, plus HeyGen's speech-to-text/text-to-speech on top (which we don't control). Acceptable in testing; E3 re-checks it.
- **Gateway mode needs a public URL:** locally that's a tunnel; in production it's the Cloud Run URL (part of E2).

---

# Part 2: Platform Foundation Deep Dive

Everything below describes the underlying platform the Resonance work builds on — the original interview POC. That POC's own legacy mode (HeyGen as its own LLM brain, resume upload + parsing, an intake/landing page) has since been removed outright — gateway mode is the only mode now — but its plumbing (session security, transcripts, GCS persistence, deployment) is reused by Resonance as-is.

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
- **`httpx`**: An async HTTP client. We use it to talk to the LiveAvatar API and to call Gemini (both the OpenAI-compatible endpoint for Host turns/summary/scoring, and the native endpoint for the Data Scout's Google Search-grounded research).
- **`google-cloud-storage`**: Persists finalized interview transcripts + summaries to a GCS bucket (when `GCS_BUCKET` is configured; otherwise we fall back to local JSON files for development).
- **`pytest` (+ `pytest-asyncio`, `respx`, `pytest-cov`)**: The test stack. `respx` mocks outbound HTTP so tests never hit LiveAvatar or Gemini for real; GCS is faked in-memory. See section 8.

### Why do we even need a backend?
You might wonder, *"Why can't the React app just talk to LiveAvatar directly?"*
**Security.** To start a LiveAvatar session, you need an API key. If we put that key in the React code, anyone could view the source code in their browser, steal the key, and rack up a massive bill on our account. The backend acts as a **Proxy**. The React app asks the backend to start a session, the backend securely uses the API key to get a temporary `session_token`, and gives that token to React.

---

## 3. Step-by-Step Architecture Walkthrough

Let's follow the data flow when a user actually uses the app today (gateway mode — the only mode).

1. **Loading the App**: The user navigates to our Cloud Run URL. The FastAPI backend receives the request and, because it's configured to serve static files, it sends the compiled React app (`index.html`, JavaScript, CSS) to the browser.
2. **Starting the Interview**: `StartScreen` `POST`s `/api/interview` (no intake form, no resume upload — that flow was removed). The backend creates an in-memory `InterviewState` with an empty vendor profile and returns an `interview_id`.
3. **Starting the Session**: The React app calls the backend's `/api/session` endpoint with that `interview_id`. The backend registers a per-interview Custom LLM with HeyGen (a secret + LLM config pointing back at our own `/llm/{interview_id}/v1` gateway) plus a minimal, generic context (no resume text — there's nothing candidate-specific to embed anymore).
4. **Token Generation**: The backend uses our secure `LIVEAVATAR_API_KEY` to ask LiveAvatar for a `session_token`. The avatar and `is_sandbox` flag come from the interview's tier: dev-tier interviews use the free sandbox avatar (`dd73ea75...`) with `is_sandbox: true`; prod-tier interviews (`/prod` URL, passcode-gated) use `PROD_AVATAR_ID` with `is_sandbox: false` and a `max_session_duration` cap (see §6).
5. **WebRTC Connection**: The backend gives the `session_token` back to React. React passes it to the `LiveAvatarSession` SDK. The SDK reaches out to LiveKit (the underlying infrastructure) and establishes a direct video/audio peer-to-peer connection. The avatar appears on screen!
6. **The Interview Itself**: Every time the vendor finishes speaking, HeyGen calls our `/llm/{interview_id}/v1/chat/completions` gateway, which drives the Host agent — a fixed question tree, phrased naturally by Gemini, that also captures the vendor's profile (name, role, company, website) conversationally as the first questions are answered.
7. **Live Transcript**: During the interview, the SDK emits `USER_TRANSCRIPTION` (vendor) and `AVATAR_TRANSCRIPTION` (interviewer) events, one per completed turn. React accumulates these into a transcript, shown live in the transcript panel.
8. **Finalizing the Interview**: When the session ends — whether the vendor stops it or LiveAvatar's server ends it (e.g. max duration) — React POSTs the captured turns to `/api/transcript/finalize`. The backend asks Gemini to write a structured Markdown summary, persists the record (summary + all turns + vendor profile + timestamp) to Google Cloud Storage (or a local JSON file), and — because this is a live interview, not a legacy/no-`interview_id` finalize — hands the interview off to the background pipeline (Scout → Evaluator → Coordinator). The summary appears on screen immediately; the scorecard, research insights, and follow-up recommendation fill in progressively as the frontend polls `GET /api/interview/{interview_id}/state` every 3 seconds until the pipeline reaches `ready` (or `failed`). The vendor can download the whole record as Markdown.

---

## 4. Deep Dive: Frontend Mechanics (`App.tsx`)

If you look inside `/liveAvatar/frontend/src/App.tsx`, here are the key concepts you need to understand:

- **Build-Time Config Flags**: `config.ts` reads `import.meta.env.VITE_SHOW_SELF_VIEW` (default on; set to `false` to hide the local camera panel and skip camera permission entirely) and `import.meta.env.VITE_SESSIONS_SHEET_URL` (optional link to an "all sessions" sheet, rendered only when set) — both are Vite build-time vars, so the `Dockerfile` plumbs them through as `ARG`/`ENV` for the frontend build stage.
- **Event Listeners (VAD)**: Voice Activity Detection (VAD) is how the system knows who is talking. The `LiveAvatarSession` object emits events like `AgentEventsEnum.AVATAR_SPEAK_STARTED` and `USER_SPEAK_STARTED`. We listen to these events to change our React state (`speakingState`), which animates the little audio bars on the screen.
- **The "Before Unload" Safety Net**: LiveAvatar only allows a certain number of concurrent sessions. If a user just closes their browser tab, the session might stay alive for a few minutes on the server, blocking others. We use the browser's `beforeunload` event with `keepalive: true` to send a final `/api/session/stop` request as the tab dies, ensuring clean cleanup.
- **Transcript Capture & Finalization**: `useLiveAvatarSession` listens for the SDK's `USER_TRANSCRIPTION`/`AVATAR_TRANSCRIPTION` events and appends each completed turn to a `useRef` array (a ref, not just state, so the transcript survives the state reset that happens during cleanup). When the session ends, its `onSessionEnd` callback hands those turns to `useInterviewSummary`, which POSTs them to `/api/transcript/finalize` and drives the `SummaryPanel`. `utils/downloadTranscript.ts` turns the summary + turns into a downloadable Markdown file.

---

## 5. Deep Dive: Backend Mechanics (`app/`)

Inside `/liveAvatar/backend/app/` (routers + services, see `CLAUDE.md` for the module layout), the logic is designed for resilience:

- **Resonance modules (see Part 1 for the concepts):** `routers/interview.py` (create interview, the live state endpoint `SummaryPanel` polls, and the text-chat fallback), `routers/llm_gateway.py` (the OpenAI-compatible endpoint HeyGen calls, with per-interview bearer-token auth and a "never return an error after auth" rule), and `services/interview_state.py`, `interview_config.py`, `host_agent.py` (live interview + onboarding), `scout_agent.py` (post-interview research), `evaluator_agent.py` (holistic scoring), `coordinator_agent.py` (threshold recommendation), `pipeline.py` (the one orchestrator sequencing Scout → Evaluator → Coordinator in the background after finalize), `llm_json.py` (shared tolerant JSON parser for all Gemini calls). Question trees and rubric live in `data/questionnaires/{domain}.yaml` / `data/rubric.yaml`.

- **Concurrency Tracking**: We maintain a simple `active_sessions_count` variable. This helps us monitor if we are hitting LiveKit's concurrency limits.
- **Garbage Collection (Contexts + LLM Configs)**: When a session stops (in `/api/session/stop`), we don't just stop the video. We also best-effort `DELETE` the per-interview context, Custom LLM configuration, and secret we created on HeyGen's side for that interview. If we didn't do this, our LiveAvatar workspace would eventually fill up with orphaned per-interview resources.
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

### Avatar tiers: /dev vs /prod (one deployment, both modes)

The avatar tier is chosen **per interview by URL path**, not per deployment: `/` (or anything except `/prod`) → **dev tier** — free sandbox avatar ("Wayne", `dd73ea75-…`), `is_sandbox: true`, HeyGen force-terminates at ~1 minute; `/prod` → **prod tier** — `PROD_AVATAR_ID` with `is_sandbox: false`, burning credits (2/minute). The prod start screen has a **session-length picker** (3/5/7/10 minutes, default 5); `PROD_MAX_SESSION_SECONDS` (default 600 s) is the hard ceiling the backend enforces (400 above it).

**Time-aware wrap-up:** the Host paces clocked (prod-tier avatar) interviews against the picked duration so the session never cuts off mid-sentence. The clock starts on the interview's first gateway turn; under 120 s remaining the Host stops asking follow-ups and keeps replies short (prompt suffix + code-forced advancement); under 60 s it skips whatever questions remain and speaks a canned closing (`host_timeup_reply`, no LLM call) — so the goodbye lands in the session's final minute. HeyGen's `max_session_duration` is set to the picked duration **plus 60 s grace** (`prod_session_grace_seconds`) and acts purely as a safety net. The frontend timer badge counts **down** on prod sessions (amber in the last minute). Dev tier and chat mode have no clock and are unaffected.

Prod tier is opt-in and passcode-gated: `POST /api/interview` with `tier: "prod"` requires the `DEMO_PASSCODE` value (403 on mismatch) and 503s unless both `PROD_AVATAR_ID` and `DEMO_PASSCODE` are configured — so a leaked URL can't burn credits. Session creation re-checks `PROD_AVATAR_ID` so a prod interview can never silently fall back to the sandbox avatar (which would also time out on LiveKit with `is_sandbox: false`). Pick a public avatar from `GET /v1/avatars/public` (no auth needed); `PROD_VOICE_ID` is optional since public video avatars carry a default voice.

Branches: `main` = stable/released, `dev` = integration. Both tiers exist in every deployment; branches no longer imply an avatar mode.

### Production deploy recipe

One-time setup: `./deploy_setup.sh` (creates the `LIVEAVATAR_API_KEY` secret in Secret Manager and binds IAM); create `GEMINI_API_KEY` and `DEMO_PASSCODE` secrets the same way (all keys travel via Secret Manager, not plain env vars), and a transcripts bucket with `roles/storage.objectAdmin` for the Cloud Run runtime SA. Then, from the repo root on the branch being released (the live 2026-07-23 deployment: service `liveavatar-demo`, project `dc-un-499210`, region `us-central1`, branch `dev`):

```bash
gcloud run deploy <service> \
  --source liveAvatar \
  --region <region> \
  --allow-unauthenticated \
  --no-cpu-throttling \
  --update-secrets "LIVEAVATAR_API_KEY=LIVEAVATAR_API_KEY:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest,DEMO_PASSCODE=DEMO_PASSCODE:latest" \
  --update-env-vars "PROD_AVATAR_ID=<public-avatar-id>,PROD_VOICE_ID=<voice-id>,DEFAULT_DOMAIN=frontier_tech,GCS_BUCKET=<bucket>,HOST_STREAMING_ENABLED=true"
```

`PUBLIC_BASE_URL` is a chicken-and-egg on the FIRST deploy only: HeyGen needs the service URL, which doesn't exist until the first deploy. So deploy once without it (session creation 503s until it's set), grab the URL from the deploy output, then:

```bash
gcloud run services update <service> --region <region> \
  --update-env-vars PUBLIC_BASE_URL=<service-url>
```

On later deploys the URL already exists — include `PUBLIC_BASE_URL=<service-url>` in the deploy's `--update-env-vars` directly.

Then `https://<service-url>/` is the free dev-tier experience and `https://<service-url>/prod` is the credit-burning demo tier (needs the passcode).

Production prerequisites: LiveAvatar credits on the account (FULL mode is 2 credits/minute) and a public (or custom) avatar ID — only if that avatar is an *image* avatar does it also need a `PROD_VOICE_ID` (video avatars have a built-in voice).

### CPU throttling and the post-interview pipeline

The post-interview pipeline (`app/services/pipeline.py`, Scout → Evaluator → Coordinator) runs as an in-process `asyncio` background task that keeps executing *after* `POST /api/transcript/finalize` has already returned its HTTP response. Cloud Run's **default request-based CPU allocation** only guarantees CPU to a container while it's actively handling a request — once the response is sent, CPU can be throttled to near zero between requests, which can stall or badly slow down that background task.

Mitigations:
- Deploy with **`--no-cpu-throttling`** (instance-based billing) so the container keeps its CPU allocation between requests. This is the clean fix, at the cost of paying for CPU while idle.
- Alternatively, rely on the frontend's 3-second `GET /api/interview/{id}/state` polling (`useInterviewSummary`) — as long as a user is watching the results view, those poll requests keep giving the instance CPU, which incidentally keeps the background pipeline task moving too. This is a weaker guarantee (no one polling = no CPU) but works for the demo/POC.
- Once the Pub/Sub seam noted in `pipeline.py` is implemented in prod (a separate push-subscriber service processes the pipeline instead of an in-process task), this concern goes away entirely — the pipeline no longer depends on the API instance's CPU allocation.

---

## 7. Troubleshooting & FAQ

**Q: The avatar connects but is completely silent and ignores speech. What's wrong?**
**A:** This is almost always a missing `context_id`, or `PUBLIC_BASE_URL` not resolving to something HeyGen can actually reach. In FULL Mode, if LiveAvatar doesn't know *what* its personality is (the context), it goes into a restricted mode where it streams video but ignores input; separately, if HeyGen can't call back into our `/llm/{interview_id}/v1` gateway, the avatar has no reply to speak. Check `PUBLIC_BASE_URL` and the backend logs for LLM-registration failures from `/api/session`.

**Q: I'm getting an "Active session already exists" error.**
**A:** A previous session didn't close cleanly (e.g., your browser crashed, bypassing the `beforeunload` event). The system is protecting you from starting a second concurrent session. You can either wait 5 minutes for the idle timeout, or manually trigger the `/api/session/stop` endpoint.

**Q: Cloud Build is failing with an error about missing modules or incompatible binaries.**
**A:** You likely accidentally uploaded your local environments. Ensure `.gcloudignore` is present in the root of the project and contains `node_modules`, `.venv`, and `.env`.

**Q: How do I switch between sandbox and production avatar mode?**
**A:** By URL: open `/` for the free sandbox tier, `/prod` for the production avatar (see "Avatar tiers" in §6). Prod needs `PROD_AVATAR_ID` + `DEMO_PASSCODE` env vars and account credits; the passcode is entered on the start screen. There is no env var that flips the whole deployment's mode — one deployment serves both tiers. (Background: pairing the sandbox avatar with `is_sandbox: false` makes LiveKit inexplicably time out, which is why the sandbox avatar is pinned to the dev tier.)

**Q: Which tunnel should I use locally?**
**A:** **ngrok with a free static domain** — the only provider that proved reliable (trycloudflare and localhost.run are SNI-blocked/reset on our network, and localtunnel's links rot within ~30 minutes, which freezes avatar sessions after the opening line). One-time: sign up at dashboard.ngrok.com, claim the free static domain, `brew install ngrok`, `ngrok config add-authtoken <token>`. Then `ngrok http 3001 --url=https://<your-domain>.ngrok-free.dev` and pin `PUBLIC_BASE_URL=https://<your-domain>.ngrok-free.dev` in `backend/.env` once — the domain never changes, so tunnel and backend restarts are fully independent. Health check: `curl -X POST <domain>/llm/test/v1/chat/completions` → **404 means healthy** (the request reached the app); 502 means the tunnel is down. Also remember: most backend iteration doesn't need a tunnel at all — text-chat mode exercises the full Host/pipeline path same-origin.

**Q: The interview ended but the summary says it couldn't be generated. Was the transcript lost?**
**A:** No. Summary generation is best-effort — if Gemini is unavailable or `GEMINI_API_KEY` is missing, the backend sets `summary_ok=False` but still saves the full transcript, and the UI still lets you download it. Check the backend logs for the summary warning. If the transcript itself failed to save (a 500), that's a real error — check `GCS_BUCKET` / credentials, or the local `transcripts/` folder's write permissions.

**Q: Where do transcripts get stored?**
**A:** If `GCS_BUCKET` is set, into that bucket at `transcripts/{session_id}.json` (using Application Default Credentials). If it's unset, into a local `backend/transcripts/` folder — this is the zero-config dev fallback and is gitignored.

---

## 8. Testing

The backend has a `pytest` suite under `/liveAvatar/backend/tests/` with a test file for every module in `app/`.

- **Run it:** from `/liveAvatar/backend`, `uv sync` then `uv run pytest` (add `--cov` for a coverage report). Config lives in `pyproject.toml` (`asyncio_mode=auto`, branch coverage on `app`, the `dev` dependency group).
- **No real network or cloud:** outbound HTTP (LiveAvatar + Gemini) is mocked with `respx`, and Google Cloud Storage is replaced by hand-rolled in-memory fakes in `tests/fakes.py`. Tests never hit a live service or need credentials.
- **Shared fixtures (`tests/conftest.py`):** `patch_settings` for overriding config per-test, a plain FastAPI `TestClient` (there's no app startup/lifespan step to skip anymore), `fake_gcs_client`, and `tmp_transcripts_dir` for the local-file transcript path.
- **CI:** `.github/workflows/ci.yml` runs this suite (plus an import-sanity check) on every push and pull request, alongside the frontend lint/build.