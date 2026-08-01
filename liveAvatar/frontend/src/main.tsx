/**
 * React entry point: mounts <App> into the #root element under StrictMode
 * and pulls in the global stylesheet. No app logic lives here - the one
 * exception is the dev-only Scout test harness path check below, since
 * there is no client-side router in this app (App itself picks between its
 * two views via plain state, not a URL).
 */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ScoutTestPage } from './components/ScoutTestPage.tsx'

// DEV-ONLY: a manual Data Scout Agent testing harness (see
// components/ScoutTestPage.tsx), reachable at /scout-test. Gated on
// import.meta.env.DEV (false in a production build via `npm run build`) so
// this never appears in a deployed build regardless of the URL requested -
// it's a local dev tool, not a real route.
const isScoutTestRoute = import.meta.env.DEV && window.location.pathname.startsWith('/scout-test')

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {isScoutTestRoute ? <ScoutTestPage /> : <App />}
  </StrictMode>,
)
