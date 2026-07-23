# Resonance — Onboarding Guide

Welcome! This is the hand-over doc for anyone picking up **Resonance** for the
first time. It's written to get you from zero to a running interview (and a
mental model of how the whole thing fits together) without having to reverse-
engineer the code first.

Read this once top-to-bottom, then keep [`CLAUDE.md`](../../CLAUDE.md) (the
dense architecture reference) and [`docs/KT.md`](KT.md) (rationale +
troubleshooting) open as you work. If something here disagrees with the code,
the code wins — please fix the doc.

---

## §1 What is Resonance?

Resonance is a proof-of-concept for an **AI-driven vendor-evaluation
interview**. A vendor representative opens a web page, and instead of a human
interviewer they talk to an AI avatar (we call her **Noor**). Noor greets them,
captures their basic details conversationally (no intake form), and walks them
through a fixed, per-domain question script — the same questions for every
vendor in that domain, so the evaluation stays fair and unbiased. When the
interview ends, a small team of background AI agents researches the company,
scores the transcript against a rubric, and recommends a next step for a human
evaluator.

The one big architectural idea worth internalizing early: **our backend is the
avatar's brain.** HeyGen's LiveAvatar (FULL Mode) normally runs the whole
conversation in HeyGen's cloud, but its **Custom LLM** feature lets us point the
avatar's "LLM" at our own FastAPI server. So every time the vendor finishes
speaking, HeyGen calls *our* endpoint — `POST /llm/{interview_id}/v1/chat/completions`,
the "gateway" — for the reply. HeyGen still does the hard real-time work
(speech-to-text, lip-synced video, text-to-speech), but the conversation logic
runs in *our* process. There is no separate agent worker — it's all in-request,
in-process, inside the one FastAPI app.

There are **four agents**. The **Host** runs the live interview: one structured
Gemini call per vendor utterance that only phrases the reply, judges whether the
answer is complete, and reports any profile details just stated — all the actual
state (which question is next, follow-up limits, merging profile updates) is
deterministic code we can unit-test, not the LLM's job. After the interview ends,
a post-interview **pipeline** (`pipeline.py`, an in-process `asyncio` task) runs
three more agents in sequence: the **Scout** researches the company on the web
(Gemini + Google Search grounding), the **Evaluator** scores the whole transcript
against the rubric in one holistic pass, and the **Coordinator** applies a
deterministic threshold rule to recommend advancing / a clarification call /
nothing. Crucially, the Scout runs **only after** the interview — it never sees
the conversation and never informs the Host's questions, by design, so the
interview can't be biased by what we find on the web.

---

## §2 Repo tour

The repo root holds `CLAUDE.md` (the architecture reference — AI-agent-oriented,
but humans welcome) and the active app under `liveAvatar/`. (A second POC,
`livekit-app/`, was removed — see git history if you ever need it.)

```
sail-live-agent/
├── CLAUDE.md                      # architecture reference (ground truth)
├── README.md                      # repo entry point → points here
└── liveAvatar/
    ├── README.md                  # app-level overview
    ├── Dockerfile                 # multi-stage: build frontend, then serve from FastAPI
    ├── deploy_setup.sh            # one-time: LIVEAVATAR_API_KEY into Secret Manager + IAM
    ├── backend/                   # Python / FastAPI (uv)
    │   ├── app/
    │   │   ├── main.py            # app wiring: CORS, routers, static/SPA mount
    │   │   ├── config.py          # ALL env vars + constants + agent prompts
    │   │   ├── models.py          # Pydantic request/response models
    │   │   ├── routers/           # HTTP layer (one file per concern)
    │   │   └── services/          # the agents + state + clients (the real logic)
    │   ├── data/
    │   │   ├── questionnaires/    # {domain}.yaml — one linear question script per domain
    │   │   └── rubric.yaml        # the global "Signal Matrix" scoring rubric
    │   ├── scripts/               # one-off ops scripts (NOT part of the served app)
    │   └── tests/                 # pytest suite, 1:1 with app/
    ├── frontend/                  # React 19 + Vite + TypeScript
    │   └── src/
    │       ├── components/        # presentational React components
    │       ├── hooks/             # session lifecycle, polling, chat, timers
    │       └── utils/             # transcript download, time formatting
    └── docs/
        ├── KT.md                  # deep dive: rationale, deploy recipe, troubleshooting/FAQ
        ├── ONBOARDING.md          # this file
        ├── llm-gateway-notes.md   # Phase-0 gateway spike findings (the observed HeyGen contract)
        └── plans/                 # HISTORICAL design docs — describe since-changed designs, NOT current
```

A few pointers so you don't have to open every file:

- **`backend/app/routers/` vs `backend/app/services/`** — routers are the thin
  HTTP layer (validate the request, call a service, shape the response);
  services hold the real logic (the four agents, interview/session state, the
  HeyGen and Gemini clients, transcript storage). When you want to understand
  *what the app does*, read `services/`; when you want to know *what endpoints
  exist*, read `routers/`.
- **`backend/scripts/`** — ops helpers you run by hand, never served:
  - `check_account.py` — prints your LiveAvatar credit balance and active
    sessions (run this **before** a credit-burning demo).
  - `smoke_test_concurrency.py` — a manual session lifecycle check (create /
    start / stop a real sandbox session); not a pytest test.
  - `cleanup_orphaned_resources.py` — purges leaked per-interview HeyGen
    resources (LLM configs / secrets / contexts) that accumulate when sessions
    end server-side. See §7.
- **`docs/plans/`** — **historical.** These are the original planning docs;
  several designs described in them have since changed (e.g. scoring moved from
  per-answer to one holistic pass). Read them for background, not as the current
  spec — `CLAUDE.md` and the code are the current spec.

---

## §3 How an interview flows

Here's the request sequence end-to-end. (Endpoints are the exact paths — there's
no router prefix.)

1. **`POST /api/interview`** — mints an `interview_id` and creates an in-memory
   `InterviewState`. Optional body `{domain, tier, passcode, duration_minutes}`;
   `domain` defaults to `frontier_tech`, `tier` to `"dev"`. This happens *before*
   any UI is shown — there's no intake form.
2. **StartScreen** — the vendor picks **avatar** (video interview) or **text
   chat**, and (in dev, as a stand-in for the admin-assigned domain) an interview
   domain.
3a. **Avatar path** — **`POST /api/session`** provisions the per-interview HeyGen
    resources: a secret (the `gateway_token`), a Custom LLM config pointing back
    at our `/llm/{id}/v1` gateway, and a minimal context — then returns a session
    token to the browser. From then on, **HeyGen calls
    `POST /llm/{id}/v1/chat/completions`** once per vendor utterance; that call
    drives the Host agent.
3b. **Chat path** — **`POST /api/interview/{id}/chat`** drives the *exact same*
    Host agent, same-origin and unauthenticated (it never leaves our backend, so
    it needs no `gateway_token` and **no tunnel**). It passes `mode="chat"` so the
    Host treats terse typed answers as complete.
4. **During the interview** — the frontend polls **`GET /api/interview/{id}/state`**
   every 5s for the live profile card. The vendor can correct captured details
   with **`PATCH /api/interview/{id}/profile`** any time before finalize.
5. **Session ends** (vendor stops it, or HeyGen ends it server-side) →
   **`POST /api/transcript/finalize`** generates the Markdown summary, saves the
   record, and (for a live `interview_id`) hands the interview to the background
   pipeline.
6. **`GET /api/interview/{id}/state`** polling (now every 3s) fills in the
   scorecard, scout insights, and recommendation progressively as the pipeline
   advances `pipeline_status` through `interviewed → scouting → evaluating →
   ready` (or `failed`).

**Mind the in-memory state.** `interview_state` (the interview registry) and
`session_state` (the active-session tracker) both live in process memory. A
backend restart **forgets all active interviews** — an in-flight interview
starts over. The session tracker is TTL-based: each tracked session expires
on its own (dev ~180s, prod `max_session_duration + 30`s) even without an
explicit release, and the frontend's `SESSION_DISCONNECTED` handler also
POSTs `/api/session/stop` when HeyGen ends a session server-side, so the
count drops immediately in the common case and the TTL bounds any remaining
drift rather than letting it accumulate until restart. The in-memory
interview registry is still known, accepted POC behavior (see §7 and
`CLAUDE.md`).

---

## §4 Running it locally

Prerequisites:
- **`uv`** (Python package manager — installs Python 3.13 for you).
- **Node 20+** (for the frontend).
- `backend/.env` with at least `LIVEAVATAR_API_KEY` and `GEMINI_API_KEY`. Copy
  it from the template: `cp backend/.env.example backend/.env` and fill in the
  keys.

### §4.1 The easy path — text chat, no tunnel needed

**Start here.** The text-chat mode drives the same Host agent and the same
post-interview pipeline (scorecard and all) entirely same-origin — so it needs
**no `PUBLIC_BASE_URL` and no tunnel**. This is the fastest way to exercise
almost the whole system, and it's the cheap end-to-end test for most backend
changes.

```bash
# Terminal 1 — backend (from liveAvatar/backend/)
uv sync
uv run uvicorn app.main:app --port 3001 --reload

# Terminal 2 — frontend (from liveAvatar/frontend/)
npm install
npm run dev
```

Open the printed Vite URL, click **"Use text chat instead"**, and interview
away. When you stop, the summary and scorecard fill in just like the avatar
path.

### §4.2 The full avatar path — needs a public URL

The avatar path only works if HeyGen can call back into our gateway, so
`PUBLIC_BASE_URL` must point at something HeyGen can reach. **Without it,
`POST /api/session` returns 503** (`"PUBLIC_BASE_URL is not configured"` — the
backend refuses to start an avatar session it knows HeyGen can't complete).
Text chat is unaffected.

**Recommended tunnel: ngrok with a free static domain.** Per `docs/KT.md` §7's
reliability findings, ngrok is the only provider that proved dependable on this
network. (`localtunnel` and `cloudflared` exist, but were found unreliable here:
localtunnel links rot in ~30 minutes and freeze avatar sessions after the
opening line; trycloudflare was SNI-blocked.) A static ngrok domain means the
URL never changes, so tunnel and backend restarts stay independent.

One-time ngrok setup: sign up at dashboard.ngrok.com, claim your free static
domain, `brew install ngrok`, then `ngrok config add-authtoken <token>`.

```bash
# Terminal 1 — tunnel (leave running)
ngrok http 3001 --url=https://<your-domain>.ngrok-free.dev

# Terminal 2 — backend, pointed at the tunnel
PUBLIC_BASE_URL=https://<your-domain>.ngrok-free.dev \
  uv run uvicorn app.main:app --port 3001 --reload
# (or just pin PUBLIC_BASE_URL in backend/.env once — the static domain never changes)

# Terminal 3 — frontend
npm run dev
```

Health check for the tunnel:
`curl -X POST https://<your-domain>.ngrok-free.dev/llm/test/v1/chat/completions`
→ **404 means healthy** (the request reached the app); a 502 means the tunnel
is down.

### Dev vs prod tier

The avatar **tier is chosen by URL path**, not by an env var:

- **`/`** → **dev tier**: the free sandbox avatar (`SANDBOX_AVATAR_ID`,
  `is_sandbox: true`). HeyGen force-terminates these at **~1 minute**. Great for
  iterating.
- **`/prod`** → **prod tier**: `PROD_AVATAR_ID` with `is_sandbox: false`, which
  **burns credits (2/minute)**. Requires `PROD_AVATAR_ID` **and** `DEMO_PASSCODE`
  to be set (otherwise 503), and the passcode is entered on the start screen
  (wrong passcode → 403). The prod start screen shows a **session-length picker**
  (default 5 min; hard ceiling `PROD_MAX_SESSION_SECONDS`, default 600s).

> Never pair the sandbox avatar with `is_sandbox: false` — LiveKit times out
> silently. That's why the sandbox avatar is pinned to the dev tier.

---

## §5 Testing & linting

### Backend

```bash
# from liveAvatar/backend/
uv run pytest            # full suite (config in pyproject.toml)
uv run pytest --cov      # with a branch-coverage report on app/
uv run ruff check .      # lint (pyflakes + core pycodestyle + import sorting)
```

Conventions:
- **1:1 test-per-module** — every module in `app/` has a matching file under
  `tests/`. If you add a module, add its test file.
- **No real network or cloud.** Outbound HTTP (LiveAvatar + Gemini) is mocked
  with `respx`; Google Cloud Storage is replaced by hand-rolled in-memory fakes
  in `tests/fakes.py`. Tests need no credentials and hit no live services.
- Shared fixtures live in `tests/conftest.py` (`patch_settings`, a plain
  `TestClient`, `fake_gcs_client`, `tmp_transcripts_dir`).

### Frontend

There is **no frontend test suite.** The only automated gates are:

```bash
# from liveAvatar/frontend/
npm run lint     # oxlint
npm run build    # tsc -b && vite build  (the TypeScript compile is the gate)
```

Because there are no unit tests, **any change to hooks or session logic needs a
manual browser pass.** The cheapest end-to-end check is the chat-mode flow from
§4.1 — it exercises the Host and the full pipeline without a tunnel.

### What CI runs (`.github/workflows/ci.yml`)

On **every pull request** and on **pushes to `main`**:
- **frontend** job: `npm ci` → `npm run lint` → `npm run build`.
- **backend** job: `uv sync --frozen` → `uv run ruff check .` → an import-sanity
  check (`import app.main`) → `uv run pytest -q`.

---

## §6 Deploying to Cloud Run

Resonance deploys as a **single Cloud Run container**: the multi-stage
`Dockerfile` builds the React frontend, then the FastAPI backend serves the
compiled static files directly (on port 8080) — no CORS, one service.

**One-time:** `./deploy_setup.sh` (from `liveAvatar/`) provisions the
`LIVEAVATAR_API_KEY` secret in Google Cloud Secret Manager and binds the IAM so
Cloud Run can read it. It reads the key from `$LIVEAVATAR_API_KEY` — export it
first, it isn't hardcoded. Create `GEMINI_API_KEY` and `DEMO_PASSCODE` as
secrets the same way (`printf '%s' "$KEY" | gcloud secrets create NAME
--data-file=-` + a `roles/secretmanager.secretAccessor` binding for the Cloud
Run runtime service account) — all three keys travel via Secret Manager, never
as plain `--set-env-vars` values. You also need a GCS bucket for transcripts
(`gcloud storage buckets create gs://<bucket> --location=<region>
--uniform-bucket-level-access`, plus `roles/storage.objectAdmin` for the same
runtime SA).

**Deploy** (mirrors `docs/KT.md` §6 — read there for the full rationale; this
is the exact shape of the live 2026-07-23 deployment, service
`liveavatar-demo` in project `dc-un-499210`). From the repo root:

```bash
gcloud run deploy <service> \
  --source liveAvatar \
  --region <region> \
  --allow-unauthenticated \
  --no-cpu-throttling \
  --update-secrets "LIVEAVATAR_API_KEY=LIVEAVATAR_API_KEY:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest,DEMO_PASSCODE=DEMO_PASSCODE:latest" \
  --update-env-vars "PROD_AVATAR_ID=<public-avatar-id>,PROD_VOICE_ID=<voice-id>,DEFAULT_DOMAIN=frontier_tech,GCS_BUCKET=<bucket>,HOST_STREAMING_ENABLED=true"
```

`--source liveAvatar` makes `liveAvatar/` the build context, so `.dockerignore`
/ `.gcloudignore` there apply. Frontend Vite flags are **build-time** — pass them
as `--build-arg VITE_SHOW_SELF_VIEW=...` / `--build-arg VITE_SESSIONS_SHEET_URL=...`
if you need non-default values.

**`PUBLIC_BASE_URL` is a chicken-and-egg on the FIRST deploy only:** HeyGen
needs the service URL, which doesn't exist until the first deploy. So deploy
once without it (avatar session creation 503s until it's set), grab the URL
from the deploy output, then:

```bash
gcloud run services update <service> --region <region> \
  --update-env-vars PUBLIC_BASE_URL=<service-url>
```

On every later deploy the URL already exists, so just include
`PUBLIC_BASE_URL=<service-url>` in the deploy's `--update-env-vars` directly.

`https://<service-url>/` is the free dev tier; `https://<service-url>/prod` is
the credit-burning demo tier (needs the passcode).

**CPU-throttling caveat.** The post-interview pipeline is an in-process
`asyncio` background task that keeps running *after* `POST /api/transcript/finalize`
has returned. Cloud Run's default request-based CPU allocation can throttle the
container to near-zero between requests, which stalls that task. `--no-cpu-throttling`
(above) is the clean fix; the frontend's 3s state-polling also incidentally keeps
the instance awake while someone's watching the results. See `docs/KT.md` §6.

**Image hygiene.** `.dockerignore` and `.gcloudignore` keep `transcripts/`,
`.env`, caches, `node_modules`, `.venv`, and `tests/` out of the image/upload.
Note these patterns are **anchored to the build-context root** (`liveAvatar/`),
so they use `**/` to match at any depth — a bare `foo/` only matches the
top level.

---

## §7 Ops runbook & gotchas

- **Check credits before a demo.** `uv run python scripts/check_account.py`
  prints your credit balance and any active sessions. Prod-tier avatar time
  burns 2 credits/minute.
- **Avatar goes quiet mid-interview? Just speak.** HeyGen cancels the avatar's
  in-flight reply whenever new user speech arrives (its VAD splits flowing
  answers into fragments, each firing a fresh gateway call), and after the last
  cancellation it may never re-request one — the avatar's next line is silently
  dropped and both sides wait for the other (diagnosed live 2026-07-22: backend
  200s, then zero further `/llm/` calls on a healthy tunnel). Saying anything
  ("shall we continue?") forces a new gateway call and resumes the script. The
  UI shows a nudge banner after ~20 s of mutual silence.
- **How the gateway survives VAD fragmentation** (also 2026-07-22, see the
  `llm_gateway`/`host_agent` docstrings): cancelled replies never enter
  HeyGen's message history, so fragments arrive as consecutive trailing
  `user` messages — `_last_user_text` joins that run back into the full
  utterance. Supersede detection is our own bookkeeping: each utterance
  request bumps `InterviewState.request_seq`, waits a settle beat
  (`HOST_UTTERANCE_SETTLE_SECONDS`, default 0.5 s — "let them finish"), and
  a turn whose seq is no longer the head is skipped before the Gemini call
  or discarded before any state mutation (a reply the vendor never heard
  must not append turns, burn follow-up budget, or advance the script).
  Don't "simplify" this to `request.is_disconnected` — it silently never
  fires through the tunnel/uvicorn stack; we tried. Turns serialize on
  `InterviewState.turn_lock`. Once the script reaches END, `/state` reports
  `done: true` and the frontend auto-stops the session ~8 s later — without
  that, every post-interview utterance re-spoke the canned closing.
- **Time-aware pacing, both directions.** Clocked (prod-tier) interviews rush
  when time runs short (<120 s: no follow-ups; <60 s: canned wrap-up) and now
  also stretch when time is ample (`HOST_TIME_GENEROUS_SECONDS`, default
  180 s remaining: brief answers get one deeper follow-up) — so a 5-minute
  booking is spent interviewing instead of ending at question seven. There is
  deliberately NO follow-up-budget force-advance anymore: an incomplete or
  non-answer turn (a correction, a question back at Noor, a fragment) stays
  on the current question however many rounds it takes — time pressure and
  the wrap-up are what move a slow interview along. The Host prompt also
  teaches Noor to handle those non-answers like a human (accept corrections,
  defer off-topic questions, never bolt the next scripted question onto such
  a reply).
- **Future turn-taking options** (if free-flow VAD ever proves insufficient):
  FULL mode exposes no VAD/endpointing tuning, but supports
  `interactivity_type: "PUSH_TO_TALK"` (explicit turn boundaries,
  whole-utterance processing — docs.liveavatar.com/docs/full-mode/push-to-talk);
  the deeper option is LITE mode with our own STT/TTS pipeline and semantic
  end-of-turn detection. Both are documented escapes, not built.
- **Concurrency counter drift (bounded).** The active-session count is now a
  TTL-tracked `SessionTracker` (dev ~180s, prod `max_session_duration + 30`s):
  when HeyGen ends a session server-side (sandbox ~1-min cap, or a prod
  `max_session_duration`), the frontend's `SESSION_DISCONNECTED` handler POSTs
  `/api/session/stop` itself, and even on the rare miss the tracked entry
  still expires on its own once its TTL elapses. Drift no longer accumulates
  until a backend restart.
- **Leaked HeyGen resources.** Those same server-ended sessions never trigger our
  cleanup, so per-interview LLM configs / gateway secrets / contexts accumulate on
  the HeyGen account. Purge them with
  `uv run python scripts/cleanup_orphaned_resources.py` (it deletes only the
  auto-generated `Resonance Host …` / `Resonance Gateway …` / `AI Interviewer w/
  Context …` resources; dashboard-created ones are left untouched).
- **Where transcripts land.** If `GCS_BUCKET` is set, records go to that bucket at
  `transcripts/{session_id}.json` (via Application Default Credentials). If unset,
  they're written as local JSON under `backend/transcripts/` — the zero-config dev
  fallback, gitignored.
- **Scout insights are download-only by design.** The Scout's findings are
  included in the downloadable Markdown record, but deliberately not rendered in
  the summary UI.
- **Never pair the sandbox avatar with `is_sandbox: false`** — LiveKit times out
  silently (see §4.2).
- **Summary failures never lose the transcript.** If Gemini can't produce the
  summary, finalize logs a warning, sets `summary_ok=false`, and still saves the
  record. A *save* failure is different — that returns a 500.

---

## §8 Consuming the API / building another frontend

The backend is entirely frontend-agnostic — the React app is just one client. If
you're building another one:

- **Interactive API reference:** run the backend and open **`/docs`** (Swagger UI)
  or fetch **`/openapi.json`**. That's the authoritative, always-current contract.
- **Minimal client lifecycle** (same as §3, from an API consumer's view):
  1. `POST /api/interview` → get `{interview_id}` (optionally send
     `{domain, tier, passcode, duration_minutes}`).
  2. For an **avatar** client: `POST /api/session` with the `interview_id` → get a
     session token, hand it to the LiveAvatar SDK; HeyGen then drives the
     conversation by calling `POST /llm/{id}/v1/chat/completions` itself.
     For a **text** client: just `POST /api/interview/{id}/chat` with `{text}` and
     render the `{reply, done}` you get back.
  3. (Optional) `GET /api/interview/{id}/state` to poll the live profile /
     pipeline status; `PATCH /api/interview/{id}/profile` to correct captured
     fields — this **409s once the interview is finalized** (a post-finalize edit
     would be silently lost).
  4. On end: `POST /api/transcript/finalize` with the captured turns, then poll
     `GET /api/interview/{id}/state` until `pipeline_status` is `ready`/`failed`.
     `GET /api/transcript/{session_id}` reads the saved record back.
- **What a client never needs:** the per-interview `gateway_token`. It's internal
  to the HeyGen↔backend auth on the `/llm/{id}/v1` gateway — a browser or API
  client never sees or sends it.

---

## §9 Porting & seams

If you ever migrate this code elsewhere, these are the clean seams:

- **`config.py`** — the single env surface. Every env var and constant (API keys,
  base URLs, avatar ids, all agent prompts, model names) lives here; nothing reads
  `os.getenv` elsewhere.
- **`transcript_store.py`** — the storage seam. GCS when `GCS_BUCKET` is set, local
  JSON otherwise; swap this one file to change backends.
- **`pipeline.py`** — `enqueue()` has an explicit comment marking exactly where a
  GCP Pub/Sub publish/subscriber would replace the in-process `asyncio` task in
  production.
- **`gemini_client.py`** — the one shared LLM HTTP path (Gemini's OpenAI-compatible
  chat endpoint) used by every agent — *except* the Scout's native
  Google-Search-grounded call in `scout_agent.py`, which is the only other LLM
  HTTP path.
- **`liveavatar_client.py`** — isolates **all** HeyGen HTTP (contexts, per-interview
  LLM configs + secrets, session tokens, stop). If HeyGen's API changes, it changes
  here.
- **In-memory registries** — `interview_state.py` and `session_state.py` are the
  pieces that would need real storage (a DB, shared cache) in any multi-instance
  deployment. Today a restart forgets everything.

---

## §10 Reading list

In roughly the order that pays off:

1. **[`CLAUDE.md`](../../CLAUDE.md)** (repo root) — the dense, module-by-module
   architecture reference. Ground truth alongside the code.
2. **[`docs/KT.md`](KT.md)** — the deep dive: design rationale, the Cloud Run
   deploy recipe (§6), and the troubleshooting/FAQ (§7, incl. the tunnel
   findings). Read this when something breaks or you need the *why*.
3. **[`docs/llm-gateway-notes.md`](llm-gateway-notes.md)** — the Phase-0 spike
   findings: the exact observed HeyGen→gateway request contract, auth, and its
   ~10-second timeout.
4. **[`docs/plans/`](plans/)** — **historical.** The original planning docs;
   background only, since several designs have changed. Don't treat as current.
