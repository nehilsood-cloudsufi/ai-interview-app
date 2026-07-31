# Resonance — Local Setup Guide

I wrote this so you can go from zero to a running interview on your own
machine in under an hour. Follow it top to bottom; every command is meant to
be copy-pasted. All work happens on the **`dev` branch** — that's our
integration branch, and it's what's deployed. Once you're running, read
`docs/ONBOARDING.md` for the full repo tour; this guide is only about getting
the thing to start.

## 1. What you'll need before starting

Install these first (macOS commands shown — use your platform's equivalent):

| Tool | Why | Install |
|---|---|---|
| git | clone the repo | you have it |
| [uv](https://docs.astral.sh/uv/) | Python package manager — it also installs Python 3.13 for you, don't install Python separately | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node 20+ & npm | frontend build/dev server | `brew install node` |
| [ngrok](https://ngrok.com) | public tunnel so HeyGen can call your laptop (avatar mode only) | `brew install ngrok`, then sign up free and `ngrok config add-authtoken <token>` |

And two API keys — ask me (Nehil) for the team's keys, or create your own:

- **`LIVEAVATAR_API_KEY`** — HeyGen LiveAvatar key (liveavatar.com dashboard).
- **`GEMINI_API_KEY`** — Google AI Studio key (aistudio.google.com).

## 2. Clone and switch to dev

```bash
git clone https://github.com/nehilsood-cloudsufi/ai-interview-app.git
cd ai-interview-app
git checkout dev
```

Everything below runs from inside `liveAvatar/` — the repo root just holds it.

## 3. Backend setup

```bash
cd liveAvatar/backend
uv sync                      # creates .venv, installs runtime + dev deps (pytest, ruff, ...)
cp .env.example .env
```

Now open `.env` and fill in the two required keys:

```
LIVEAVATAR_API_KEY=<the HeyGen key>
GEMINI_API_KEY=<the Gemini key>
```

Leave everything else commented out for now. Two things to know about this
file:

- `.env` is gitignored — never commit it, and never paste its values anywhere.
- Our config loads it with `override=True`, meaning **`.env` values beat
  shell-exported variables**. If a setting mysteriously won't change, check
  `.env` first.

Sanity-check the install before going further:

```bash
uv run pytest      # full suite, should be all green in ~5s
uv run ruff check .
```

If those pass, your backend environment is correct.

## 4. Frontend setup

```bash
cd ../frontend     # i.e. liveAvatar/frontend
npm install
npm run lint       # oxlint, should pass clean
```

No `.env` needed for the frontend in normal dev — it defaults to a backend at
`http://localhost:3001` (see `src/config.ts`).

## 5. First run — text-chat mode (no tunnel, do this first)

Chat mode drives the exact same interview agent as the avatar, but everything
stays on your machine — so it needs no ngrok, no HeyGen session, no credits.
It's how I verify a fresh setup.

Terminal 1 (backend):

```bash
cd liveAvatar/backend
uv run uvicorn app.main:app --port 3001 --reload
```

Terminal 2 (frontend):

```bash
cd liveAvatar/frontend
npm run dev
```

Open http://localhost:5173, click **"Use text chat instead"**, and have a
short interview with Noor. If she replies, your Gemini key and the whole
backend agent stack work. End the interview — you should get a summary, then
a scorecard, and a JSON record lands in `backend/transcripts/` (the local
dev fallback; the deployed service writes to GCS instead).

## 6. Avatar mode — needs the tunnel

The avatar is driven by HeyGen's servers, and on every turn HeyGen calls
**back into your backend** at `/llm/{interview_id}/v1`. That callback needs a
public URL, which is what ngrok provides.

Terminal 3:

```bash
ngrok http 3001
```

Copy the `https://xxxx.ngrok-free.dev` URL it prints, put it in
`backend/.env`:

```
PUBLIC_BASE_URL=https://xxxx.ngrok-free.dev
```

and restart the backend (Ctrl-C in terminal 1, run uvicorn again — `--reload`
does not re-read `.env`). Now http://localhost:5173 → **Start Interview**
gives you the avatar. Two expectations for local dev:

- You're on the **free sandbox avatar** — HeyGen force-ends the session at
  ~1 minute. That's the sandbox limit, not a bug.
- If session creation fails with a **503**, `PUBLIC_BASE_URL` is missing or
  stale (ngrok free URLs change on every restart unless you claim a static
  domain — I recommend claiming one, it's free, then the URL never rots).

The `/prod` tier (real avatar, credits) also works locally if you set
`PROD_AVATAR_ID` + `DEMO_PASSCODE` in `.env`, but you won't need it for
day-to-day development — I test prod behavior against the deployed service.

## 7. Gotchas I've personally hit (so you don't)

- **Port 3001 already taken / changes not taking effect:** a previous uvicorn
  is still running and silently won the port. `pgrep -fl uvicorn`, kill it,
  restart. If behavior looks stale, always suspect this first.
- **`.env` beats your shell:** `PUBLIC_BASE_URL=... uv run uvicorn ...` will
  be *overridden* by a `PUBLIC_BASE_URL` line in `.env`. Pick one place —
  I keep everything in `.env`.
- **Avatar connects but never answers:** your tunnel is down or pointing at a
  dead backend. Quick health check — this should return **404** (yes, 404
  means healthy: the route exists, the interview id doesn't):
  `curl -s -o /dev/null -w "%{http_code}" https://<your-tunnel>/llm/test/v1/chat/completions -X POST`
- **Tests suddenly red after you edit `.env`:** they shouldn't be — the suite
  is insulated from local `.env` values — but if you add a *new* env-driven
  setting, follow the pattern in `tests/conftest.py` so your `.env` can't
  leak into CI-green tests.

## 8. Where to go from here

- `docs/ONBOARDING.md` — the full tour: architecture, every module, deploy
  recipe, ops runbook. Read §1–3 on your first day.
- `CLAUDE.md` (repo root) — the same map in condensed form; also what our AI
  tooling reads.
- `docs/TESTER_GUIDE.md` — the manual test plan for the deployed service.
- `docs/KT.md` — background and rationale for the big design decisions.

Branch etiquette: feature branches off `dev`, PRs into `dev`, and `main` is
release-only. CI (pytest + ruff + frontend lint/build) runs on every push —
keep it green.

— Nehil
