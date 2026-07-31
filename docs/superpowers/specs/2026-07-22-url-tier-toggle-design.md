# URL-based dev/prod tier toggle

Date: 2026-07-22 · Status: approved (interview with Nehil, this session)

## Problem

Sandbox sessions are free but hard-capped by HeyGen at ~1 minute with a fixed stock avatar ("Wayne"). Demos need 5–10 minute sessions with a chosen public avatar ("June HR", `65f9e3c9-d48b-4118-b73a-4ae2e3cbb8f0`), which requires `is_sandbox: false` + account credits (2/min; account has ~1010). The previously merged env-var toggle (PR #7, `SANDBOX_MODE`) bakes one mode into each deployment — switching means a redeploy/restart. We want both modes live in one deployment, chosen per interview by URL.

## Decision (interview outcomes)

- **Tier by URL path**: `/` and anything ≠ `/prod` → `dev` tier; `/prod` → `prod` tier. One app, one deployment, both tiers.
- **Naming**: `tier: "dev" | "prod"` (NOT "mode" — `InterviewMode`/`mode` already means avatar-vs-chat in this codebase).
- **dev tier**: today's behavior byte-for-byte — sandbox avatar `dd73ea75-…`, `is_sandbox: true`, no duration field.
- **prod tier**: `PROD_AVATAR_ID` env avatar, `is_sandbox: false`, `max_session_duration` = `PROD_MAX_SESSION_SECONDS` (default 600 → ≤ ~20 credits/session), optional `PROD_VOICE_ID` (public avatars have default voices, so usually unset).
- **Guard**: prod interviews require a shared passcode (`DEMO_PASSCODE` env). If `DEMO_PASSCODE` or `PROD_AVATAR_ID` is unset, prod tier is disabled (503). Wrong passcode → 403. All checks at `POST /api/interview`; session creation re-checks `PROD_AVATAR_ID` (503) in case config changed.
- **Supersedes PR #7**: `SANDBOX_MODE` and the `AVATAR_ID` env override are removed; `settings.avatar_id` stays as the dev-tier (sandbox) avatar constant. Branches main/dev remain purely code-stability branches.
- **Tunnel**: localtunnel (`npx -y localtunnel --port 3001`) is the documented local tunnel (network now SNI-blocks trycloudflare). Cloud Run needs none.

## Design

Backend (`liveAvatar/backend`):
- `config.py`: remove `sandbox_mode`; `avatar_id` back to plain sandbox default (no env). Add `prod_avatar_id` (`PROD_AVATAR_ID`), `prod_voice_id` (`PROD_VOICE_ID`), `demo_passcode` (`DEMO_PASSCODE`), `prod_max_session_seconds` (`PROD_MAX_SESSION_SECONDS`, int, 600).
- `models.py`: `CreateInterviewRequest` += `tier: str | None` (None → "dev"), `passcode: str | None`.
- `interview_state.py`: `InterviewState.tier: str = "dev"`; `create(profile, domain, tier)`.
- `routers/interview.py`: validate tier (400 unknown), prod guards (503 unconfigured / 403 bad passcode).
- `liveavatar_client.create_session_token`: takes explicit `avatar_id`, `is_sandbox`, `voice_id=None`, `max_session_duration=None` (keyword-only); no more settings lookup for these. `voice_id` goes in `avatar_persona`, `max_session_duration` top-level, omitted when None.
- `routers/sessions.py`: derive the four values from `state.tier`; replace PR #7's sandbox-avatar guard with the prod re-check.

Frontend (`liveAvatar/frontend`):
- `config.ts`: `export const TIER` from `window.location.pathname.startsWith('/prod')`.
- `StartScreen.tsx`: on prod tier show a passcode input + "production" badge; always send `{domain?, tier, passcode?}` in `POST /api/interview`; surface 403/503 messages.
- `main.py`: explicit `GET /prod` route serving the SPA `index.html` (StaticFiles(html=True) only serves `/`).

Docs: `.env.example` (PROD_* + DEMO_PASSCODE replace SANDBOX_MODE/AVATAR_ID), KT.md (tier FAQ, localtunnel, deploy env vars), CLAUDE.md.

## Testing

Existing suite (308) adapted: config env parsing; client payload per-tier fields; interview router tier/passcode guards; sessions per-tier token payload + prod 503. Manual: dev tier via rig unchanged; prod tier E2E once user runs a credit-burning session (their call).

## Out of scope

Real auth (IAP), per-user passcodes, voice_agent-based sessions, `start_rig.sh`, second Cloud Run service.
