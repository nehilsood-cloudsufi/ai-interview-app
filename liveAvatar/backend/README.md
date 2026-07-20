# Resonance — Backend

FastAPI backend (managed with [`uv`](https://docs.astral.sh/uv/)) for the Resonance
vendor-interview POC. HeyGen's LiveAvatar calls back into this server as its LLM
(the `/llm/{interview_id}/v1` gateway), so the Host agent and the whole
post-interview pipeline (Data Scout → Evaluator → Coordinator) run here. In
production it also serves the compiled React frontend as a single Cloud Run container.

## Setup

```bash
uv sync   # installs runtime + dev dependencies
cp .env.example .env   # then fill in your keys
```

## Environment

| Variable               | Required | Purpose                                                                 |
| ---------------------- | -------- | ----------------------------------------------------------------------- |
| `LIVEAVATAR_API_KEY`   | yes      | HeyGen/LiveAvatar API key (session tokens, per-interview LLM configs).   |
| `GEMINI_API_KEY`       | yes      | Gemini key — Host turns, scout research, evaluation, summary.            |
| `PUBLIC_BASE_URL`      | yes\*    | Public URL HeyGen calls back into for the LLM gateway. \*Required to create avatar sessions; use a tunnel in dev. |
| `GCS_BUCKET`           | no       | If set, transcripts are stored in this GCS bucket (ADC). If unset, local JSON under `./transcripts/`. |
| `SCOUT_ENABLED`        | no       | Default `true`. Set `false` to skip the Data Scout's web research.       |
| `HOST_STREAMING_ENABLED` | no     | Default `false`. Stream the Host's reply to HeyGen token-by-token.       |

Gemini model names (`GEMINI_MODEL`, `GEMINI_PRO_MODEL`, their `*_FALLBACK` pins) and
the questionnaire/rubric paths are also env-overridable — see `app/config.py`.

## Running

```bash
# dev: gateway sessions need a public callback URL, e.g. a cloudflared tunnel
cloudflared tunnel --url http://localhost:3001   # note the printed URL
PUBLIC_BASE_URL=https://<tunnel-host> uv run uvicorn app.main:app --port 3001 --reload
```

No one-time provisioning step — per-interview HeyGen resources (secret, LLM config,
context) are created when a session starts and deleted on stop.

## Tests

```bash
uv run pytest          # full suite (config in pyproject.toml)
uv run pytest --cov    # with coverage report
```

Tests mock all outbound HTTP (`respx`) and fake Google Cloud Storage in memory
(`tests/fakes.py`), so they need no credentials and hit no live services. CI runs the
same suite on every push/PR (`.github/workflows/ci.yml`).

## Layout

- `app/main.py` — app wiring (CORS, routers, static/SPA mount).
- `app/routers/` — `interview.py` (create interview, chat fallback, state polling),
  `sessions.py` (HeyGen session lifecycle), `llm_gateway.py` (the OpenAI-compatible
  endpoint HeyGen calls per utterance), `transcripts.py` (finalize + read back),
  `concurrency.py`.
- `app/services/` — the four agents (`host_agent`, `scout_agent`, `evaluator_agent`,
  `coordinator_agent`), `pipeline.py` (the only orchestrator; background task with
  `pipeline_status` tracking), interview state/config, LiveAvatar client, Gemini
  client, transcript store (GCS/local), summary generation.
- `app/config.py`, `app/models.py` — settings (incl. every agent prompt) and Pydantic models.
- `data/` — `questionnaire.yaml` (linear question script) and `rubric.yaml` (scoring weights).
- `scripts/` — one-off ops scripts (not part of the served app).
- `tests/` — pytest suite (1:1 with `app/`).

See `../docs/KT.md` for the full architecture walkthrough and `../../CLAUDE.md` for
module-level notes and known constraints.
