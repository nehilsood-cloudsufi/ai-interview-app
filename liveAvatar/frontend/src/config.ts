/**
 * Frontend runtime + build-time configuration. Collects the backend API base,
 * the URL-derived avatar tier, and the Vite build flags in one place so the
 * rest of the app imports named constants rather than reading import.meta.env.
 */

export const API_URL = import.meta.env.PROD ? '' : (import.meta.env.VITE_API_URL || 'http://localhost:3001');

// Avatar tier, chosen by URL path: /prod → production avatar (passcode-gated,
// credit-burning, longer sessions); anything else → free sandbox avatar
// (~1-min sessions). Distinct from InterviewMode (avatar vs text chat).
export const TIER: 'dev' | 'prod' = window.location.pathname.startsWith('/prod') ? 'prod' : 'dev';

// Self-view (local camera panel + camera toggle). Default ON; set
// VITE_SHOW_SELF_VIEW=false to hide it and skip camera permission entirely.
export const SHOW_SELF_VIEW = import.meta.env.VITE_SHOW_SELF_VIEW !== 'false';

// Optional link to the "all sessions" sheet. Rendered only when set.
export const SESSIONS_SHEET_URL = import.meta.env.VITE_SESSIONS_SHEET_URL || '';
