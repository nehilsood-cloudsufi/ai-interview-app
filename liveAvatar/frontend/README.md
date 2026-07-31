# Resonance — Frontend

React 19 + Vite + TypeScript + Tailwind frontend for the Resonance vendor-interview POC. `App.tsx` is composition-only: a start screen picks avatar or text-chat mode, hooks own the logic (`useLiveAvatarSession`, `useChatInterview`, `useInterviewSummary`, …), components are presentational. See `../../CLAUDE.md` for the walkthrough and `../docs/ONBOARDING.md` for the guided tour.

- **Commands:** `npm install`, `npm run dev`, `npm run build`, `npm run lint`.
- **Env (build-time, see `.env.example`):** `VITE_API_URL` (dev only; prod is same-origin), `VITE_SHOW_SELF_VIEW` (default on; `false` hides the self-view and skips camera permission), `VITE_SESSIONS_SHEET_URL` (optional sessions-sheet link). In the Docker build these are passed as `--build-arg`.
- **Linting:** oxlint, configured in `.oxlintrc.json`. `npm run build` also runs `tsc -b` (with `noUnusedLocals`), which is the de-facto dead-code gate — there is no frontend test suite, so keep the build green.
