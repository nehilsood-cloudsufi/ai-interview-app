# Resonance — AI Vendor-Interview POC (LiveAvatar)

This directory contains a Proof of Concept for **Resonance**: an AI-driven vendor-evaluation interview. A vendor representative talks to an AI avatar (HeyGen LiveAvatar **FULL Mode**, pointed at our own backend via HeyGen's Custom LLM feature), and once the interview ends a pipeline of background agents researches the company, scores the transcript against a rubric, and recommends a next step for a human evaluator.

## Architecture

- **Backend (`/backend`)**: A Python FastAPI server managed with `uv`. It hosts the four-agent architecture — the **Host** (drives the live interview through a fixed linear question script, over the vendor profile and context captured up front by the start screen's intake form), the **Data Scout** (post-interview company research via Gemini + Google Search grounding), the **Evaluator** (one holistic pro-model scoring pass over transcript + scout findings), and the **Coordinator** (pure threshold rule → recommendation). `pipeline.py` sequences the last three as an in-process background task after finalize, tracking a `pipeline_status` the UI polls. Transcripts + summaries persist to Google Cloud Storage (or local JSON when no bucket is configured). Ships with a pytest suite (`tests/`) covering every module.
- **Frontend (`/frontend`)**: A React 19 + Vite + Tailwind app using `@heygen/liveavatar-web-sdk`. A minimal start screen offers two modes: the avatar video interview, or a low-bandwidth **text-chat fallback** driving the exact same Host agent (also reachable one-way mid-session when network quality degrades). The end-of-interview summary view fills in progressively (Scouting → Evaluating → Ready) as the backend pipeline completes.

## Getting Started

New to the project? The full walkthrough (repo tour, interview flow, local setup, deploy, ops) is in **[`docs/ONBOARDING.md`](docs/ONBOARDING.md)**. The short version:

1. Copy `backend/.env.example` to `backend/.env` and fill in your keys.
2. **Easiest path — no tunnel needed:** start both servers and pick **"Use text chat instead"** on the start screen. Text-chat mode drives the same Host agent and the full post-interview pipeline (scorecard and all) same-origin, so it needs no `PUBLIC_BASE_URL`.
   - Backend (from `backend/`): `uv run uvicorn app.main:app --port 3001 --reload`
   - Frontend (from `frontend/`): `npm run dev`
3. **Full avatar path:** the avatar needs a public URL HeyGen can call back into — run a tunnel (**ngrok** with a free static domain is recommended; see `docs/ONBOARDING.md` §4.2 for why + alternatives) and set `PUBLIC_BASE_URL` to it when starting the backend, e.g. `PUBLIC_BASE_URL=https://<your-domain>.ngrok-free.dev uv run uvicorn app.main:app --port 3001 --reload`.
4. (Optional) Run the backend tests: `uv run pytest` (or `uv run pytest --cov` for coverage).

No one-time provisioning step is needed — the backend registers a per-interview Custom LLM config + secret with HeyGen automatically when a session starts.

> **Transcript storage:** Set `GCS_BUCKET` in the backend `.env` to persist finalized transcripts + summaries to Google Cloud Storage (uses Application Default Credentials). If unset, records are written as local JSON under `backend/transcripts/` (dev fallback, gitignored).

## Deployment

The application is configured for a Single Unified Cloud Run Service deployment. The FastAPI backend serves the compiled React static files on Port 8080 to avoid CORS issues.

- **Docker:** A multi-stage `Dockerfile` handles building the Node/React frontend and the Python backend. The frontend feature flags are **build-time** args: pass `--build-arg VITE_SHOW_SELF_VIEW=...` / `--build-arg VITE_SESSIONS_SHEET_URL=...` if you need non-default values.
- **Google Cloud Run:** Use `gcloud run deploy`. The `.gcloudignore` file ensures local `.env`, `node_modules`, and `.venv` are not uploaded to Cloud Build. **Note:** the post-interview pipeline runs as a background task after the finalize response — deploy with `--no-cpu-throttling` (or rely on the UI's status polling keeping the instance active); see `docs/KT.md` §6.
- **Secrets Management:** The `deploy_setup.sh` script automates the creation of a Google Cloud Secret Manager secret (`LIVEAVATAR_API_KEY`) and binds the necessary IAM policies.

## Features

- **Pre-interview intake:** A short form on the start screen captures name and company (required), role and a free-text "about you" note (optional), and up to 3 context documents (.pdf/.docx/.txt/.md, trimmed to 3,000 words with a notice, summarized into the vendor context) — the avatar greets the vendor by name and goes straight into question one, with no conversational onboarding step.
- **Fixed, unbiased interview script:** A linear per-domain questionnaire (`backend/data/questionnaires/{domain}.yaml`); every vendor in a domain gets the same questions, and scout research never reaches the interviewer.
- **Text-chat fallback:** A claude.ai-style chat UI for low-bandwidth situations — selectable up front, or suggested automatically when network quality drops mid-call (one-way avatar → chat switch that carries the transcript over).
- **Live transcript:** Interviewer/candidate turns captured in real time from the SDK's transcription events, in an internally-scrolling panel.
- **Post-interview agent pipeline:** Scout → Evaluator → Coordinator run in the background immediately after the interview; the summary view shows the pipeline status and fills in the rubric scorecard, research findings, and follow-up recommendation as they land. Failures never lose the transcript.
- **Downloadable record:** Summary + scorecard + full transcript as a Markdown file; the full JSON record persists to GCS/local.
- **Feature flags:** `VITE_SHOW_SELF_VIEW` (hide the vendor's self-view and skip the camera permission entirely), `VITE_SESSIONS_SHEET_URL` (optional "all sessions" link).
- **Safe session cleanup:** Per-interview HeyGen resources (LLM config, secret, context) are deleted on stop; orphaned sessions are cleaned up on tab close.
