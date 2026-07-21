# Resonance — Frontend

React 19 + Vite + TypeScript + Tailwind frontend for the Resonance vendor-interview POC. `App.tsx` is composition-only: a start screen picks avatar or text-chat mode, hooks own the logic (`useLiveAvatarSession`, `useChatInterview`, `useInterviewSummary`, …), components are presentational. See `../../CLAUDE.md` for the walkthrough.

- **Commands:** `npm install`, `npm run dev`, `npm run build`, `npm run lint`.
- **Env (build-time, see `.env.example`):** `VITE_API_URL` (dev only; prod is same-origin), `VITE_SHOW_SELF_VIEW` (default on; `false` hides the self-view and skips camera permission), `VITE_SESSIONS_SHEET_URL` (optional sessions-sheet link). In the Docker build these are passed as `--build-arg`.

---

# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the Oxlint configuration

If you are developing a production application, we recommend enabling type-aware lint rules by installing `oxlint-tsgolint` and editing `.oxlintrc.json`:

```json
{
  "$schema": "./node_modules/oxlint/configuration_schema.json",
  "plugins": ["react", "typescript", "oxc"],
  "options": {
    "typeAware": true
  },
  "rules": {
    "react/rules-of-hooks": "error",
    "react/only-export-components": ["warn", { "allowConstantExport": true }]
  }
}
```

See the [Oxlint rules documentation](https://oxc.rs/docs/guide/usage/linter/rules) for the full list of rules and categories.
