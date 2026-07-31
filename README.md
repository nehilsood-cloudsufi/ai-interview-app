# sail-live-agent

This repository holds **Resonance** — a proof-of-concept for an AI-driven
vendor-evaluation interview, built on HeyGen's LiveAvatar (FULL Mode). A vendor
representative talks to an AI avatar; when the interview ends, a pipeline of
background agents researches the company, scores the transcript against a
rubric, and recommends a next step for a human evaluator.

The active application lives in **[`liveAvatar/`](liveAvatar/)** (a FastAPI
backend + a React/Vite frontend, deployed together as a single Cloud Run
container).

## New here?

**Start with [`liveAvatar/docs/ONBOARDING.md`](liveAvatar/docs/ONBOARDING.md)** —
the full onboarding guide: what Resonance is, a repo tour, how an interview
flows, and how to run it locally. (Quickest local start: the text-chat mode
needs no tunnel and exercises the whole pipeline.)

## Where things are

- **[`liveAvatar/README.md`](liveAvatar/README.md)** — app-level overview
  (architecture, getting started, deployment).
- **[`liveAvatar/docs/KT.md`](liveAvatar/docs/KT.md)** — knowledge-transfer deep
  dive: design rationale, the Cloud Run deploy recipe, and troubleshooting/FAQ.
- **[`CLAUDE.md`](CLAUDE.md)** — the module-by-module architecture reference.
  It's written for AI coding agents, but it's the densest, most precise map of
  the codebase, so humans are welcome to read it too.
