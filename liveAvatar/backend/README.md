# LiveAvatar Interview — Backend

FastAPI backend (managed with [`uv`](https://docs.astral.sh/uv/)) for the LiveAvatar
FULL Mode interview POC. It acts as a secure proxy to the LiveAvatar API and handles
resume parsing, session lifecycle, and interview transcript/summary persistence. In
production it also serves the compiled React frontend as a single Cloud Run container.

## Setup

```bash
uv sync   # installs runtime + dev dependencies
cp .env.example .env   # then fill in your keys
```

## Environment

| Variable             | Required | Purpose                                                                 |
| -------------------- | -------- | ----------------------------------------------------------------------- |
| `LIVEAVATAR_API_KEY` | yes      | HeyGen/LiveAvatar API key (session tokens, contexts).                   |
| `GEMINI_API_KEY`     | yes\*    | Gemini key for the LLM config and interview summaries. \*Falls back to HeyGen's own AI / skips summaries if unset. |
| `GCS_BUCKET`         | no       | If set, transcripts are stored in this GCS bucket (ADC). If unset, they go to local JSON under `./transcripts/`. |

## Running

```bash
uv run python scripts/setup_gemini_context.py   # one-time: provision Gemini LLM config + base context
uv run uvicorn app.main:app --port 3001 --reload
```

## Tests

```bash
uv run pytest          # full suite (config in pyproject.toml)
uv run pytest --cov    # with coverage report
```

Tests mock all outbound HTTP (`respx`) and fake Google Cloud Storage in memory
(`tests/fakes.py`), so they need no credentials and hit no live services. CI runs the
same suite on every push/PR (`.github/workflows/ci.yml`).

## Layout

- `app/main.py` — app wiring (lifespan, CORS, routers, static/SPA mount).
- `app/routers/` — `sessions.py`, `resume.py`, `concurrency.py`, `transcripts.py`.
- `app/services/` — LiveAvatar client, Gemini provisioning, resume parsing, session
  counter, transcript store (GCS/local), and summary generation.
- `app/config.py`, `app/models.py` — settings and Pydantic models.
- `scripts/` — one-off ops scripts (not part of the served app).
- `tests/` — pytest suite (1:1 with `app/`).

See `../docs/KT.md` for the full architecture walkthrough and `../../CLAUDE.md` for
module-level notes and known constraints.
