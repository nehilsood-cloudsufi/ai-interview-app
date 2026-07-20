# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository structure

This repo currently holds one active POC, `liveAvatar/`, for an AI-driven interview experience built on HeyGen's LiveAvatar **FULL Mode** SDK, where the LLM/conversation logic runs entirely on HeyGen's infrastructure. The local backend is just a secure proxy (session/token creation, resume parsing) — there is no separate agent worker process.

It has its own `backend/` (Python/FastAPI, dependency-managed with `uv`) and `frontend/` (React 19 + Vite + TypeScript, `oxlint` for linting).

(A second POC, `livekit-app/`, built directly on the LiveKit Agents framework, was removed — see git history if you need to resurrect it.)

## `liveAvatar/backend/`

Package layout: `app/main.py` wires up the FastAPI app (lifespan, CORS, routers, static/SPA mount) — no separate agent worker process. Routes live in `app/routers/` (`concurrency.py`, `resume.py`, `sessions.py`, `transcripts.py`), each backed by `app/services/`:
- `liveavatar_client.py` — the LiveAvatar HTTP calls (create/delete context, create session token with Gemini-fallback retry, stop session).
- `gemini_provisioning.py` — on app startup, auto-provisions a Gemini LLM configuration against the LiveAvatar API if `GEMINI_API_KEY` + `LIVEAVATAR_API_KEY` are set (falls back to HeyGen's own AI silently on any failure — check logs, not exceptions, if Gemini isn't being used); tears it down on shutdown.
- `resume_parser.py` — PDF/DOCX/TXT text extraction.
- `session_state.py` — in-memory active-session counter. It only decrements on an explicit `/api/session/stop` call (user clicks stop, or the frontend's orphaned-session cleanup). When LiveAvatar's server ends a session on its own (e.g. `MAX_DURATION_REACHED`), the frontend's `SESSION_DISCONNECTED` handler resets local UI state but never calls `/api/session/stop` — so the counter drifts upward over repeated sessions and only resets on backend restart. Verified live; this is original behavior, not introduced by the `app/` restructure.
- `transcript_store.py` — persists finalized interview records. Uses **Google Cloud Storage** when `GCS_BUCKET` is set (blob `transcripts/{session_id}.json`), otherwise writes local JSON to `transcripts_local_dir` (dev fallback, gitignored). Sync GCS/file work is offloaded via `asyncio.to_thread`; `google.cloud.storage` is imported lazily inside the functions. No try/except here — failures propagate to the router, which returns 500.
- `summary_service.py` — generates the interview summary by calling Gemini's OpenAI-compatible chat endpoint through the shared `gemini_client` helper (no SDK), on the pro-tier model (`gemini_pro_model`). Deliberately raises on every failure path; the `transcripts` router is responsible for soft-failing so a summary failure never loses the transcript.
- `gemini_client.py` — the one shared Gemini chat POST used by every agent. The configured models are `-latest` aliases (`gemini-flash-latest` fast tier, `gemini-pro-latest` for finalize-time scoring/summary); on a model-not-found 4xx it retries once with the pinned `*_fallback` model from config, so an alias hot-swap can never take the app down.
- `appraiser_agent.py` — `score_interview` runs ONCE at finalize: a single holistic pro-model pass over the whole transcript producing the rubric scorecard (per-answer live scoring was deliberately removed — the vendor must not watch their own scores mid-interview, and whole-transcript judgment beats per-answer judgment). Raises on failure; the finalize route soft-fails to `scorecard: null` and still saves the transcript.

The transcript flow: the `POST /api/transcript/finalize` route calls `summary_service.generate_summary(...)` inside a try/except (on failure → `summary=""`, `summary_ok=False`, logged as a warning, transcript still saved), then `transcript_store.save(...)` (on failure → 500). `GET /api/transcript/{session_id}` reads a saved record back (404 if absent).

`app/config.py` centralizes all env vars and constants (API keys, base URL, avatar id, prompt). Transcript/summary config also lives here: `gcs_bucket` (`GCS_BUCKET` env — GCS vs local backend switch), `transcripts_local_dir`, `gemini_base_url`, the four Gemini model settings (`gemini_model`/`gemini_pro_model` `-latest` aliases + their pinned `*_fallback` names, all env-overridable), and `interview_summary_prompt` (the Markdown summary system prompt). `app/models.py` has the Pydantic request/response models (incl. `TranscriptTurn`, `FinalizeTranscriptRequest`/`Response`). One-off ops scripts (Gemini/context setup, account inspection, cleanup polling, a manual concurrency smoke test) live in `scripts/`, not part of the served app.

`tests/` holds a pytest suite with 1:1 coverage of every `app/` module (respx for HTTP mocking, hand-rolled in-memory GCS fakes in `tests/fakes.py`, shared fixtures in `tests/conftest.py`). pytest/coverage config is in `pyproject.toml` (`dev` dependency group, `asyncio_mode=auto`, branch coverage on `app`). `.github/workflows/ci.yml` runs this suite on push/PR.

Known constraints (see `docs/KT.md` for full rationale/troubleshooting):
- `is_sandbox` must stay `True` while using the default sandbox avatar ID — pairing a sandbox avatar with `is_sandbox: False` causes LiveKit to time out.
- The frontend hardcodes fallback context/LLM IDs (`frontend/src/config.ts`) because `.env` is deliberately excluded from the deployed container — without them the avatar connects but stays silent.
- The `/api/upload-resume` route collapses every parsing error (including unsupported file type and PDF page-limit) to a generic "Failed to read {filename}" 400 — this is existing behavior, not a bug to fix blindly if you're touching that route.
- Deployed as a **single** Cloud Run container: the multi-stage `Dockerfile` builds the frontend, then the FastAPI backend serves the compiled static files directly (avoids CORS). `.gcloudignore` must exclude `node_modules`/`.venv`/`.env` or Cloud Build fails on incompatible local binaries.

Commands (run from `liveAvatar/backend/`):
```bash
uv sync                                                  # installs runtime + dev deps (pytest, respx, ...)
uv run python scripts/setup_gemini_context.py           # provisions Gemini LLM config + base LiveAvatar context
uv run uvicorn app.main:app --port 3001 --reload
uv run pytest                                            # run the test suite (config in pyproject.toml)
uv run pytest --cov                                      # with coverage report
uv run python scripts/smoke_test_concurrency.py          # manual concurrency/session-lifecycle check (not pytest)
```

## `liveAvatar/frontend/`

`App.tsx` is composition-only: it wires six hooks (`hooks/`) — `useLiveAvatarSession` (SDK session lifecycle, mic/camera, orphaned-session cleanup, and transcript capture via the SDK's `USER_TRANSCRIPTION`/`AVATAR_TRANSCRIPTION` events into a ref that survives cleanup), `useResumeFiles`, `useNetworkQuality`, `useConcurrencyPoll`, `useSessionTimer`, and `useInterviewSummary` — into presentational `components/` (`ResumeUpload`, `AvatarVideoPanel`, `LocalVideoPanel`, `SessionControls`, `TranscriptPanel`, `SummaryPanel`, etc.). When a session ends (user-stopped or server-ended), `useLiveAvatarSession`'s `onSessionEnd` hands the captured turns to `useInterviewSummary.finalize`, which POSTs to `/api/transcript/finalize`; `SummaryPanel` renders the returned summary and `utils/downloadTranscript.ts` builds a downloadable Markdown record. `config.ts` holds `API_URL` and the fallback context/LLM IDs mentioned above.

Commands (run from `liveAvatar/frontend/`): `npm install`, `npm run dev`, `npm run build`, `npm run lint`.

Required env vars: `LIVEAVATAR_API_KEY`, `GEMINI_API_KEY` (`backend/.env.example`). Optional: `GCS_BUCKET` (enables the GCS transcript backend; local JSON files are used when unset).

Deployment: `deploy_setup.sh` provisions the `LIVEAVATAR_API_KEY` secret in Google Cloud Secret Manager and binds IAM so Cloud Run can read it (reads the key from `$LIVEAVATAR_API_KEY`, does not hardcode it) — run this before `gcloud run deploy`. `.github/workflows/ci.yml` runs frontend lint/build and, for the backend, an import-sanity check plus the full pytest suite on push/PR.
