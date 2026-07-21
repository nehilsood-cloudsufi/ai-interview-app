export const API_URL = import.meta.env.PROD ? '' : (import.meta.env.VITE_API_URL || 'http://localhost:3001');

// Self-view (local camera panel + camera toggle). Default ON; set
// VITE_SHOW_SELF_VIEW=false to hide it and skip camera permission entirely.
export const SHOW_SELF_VIEW = import.meta.env.VITE_SHOW_SELF_VIEW !== 'false';

// Optional link to the "all sessions" sheet. Rendered only when set.
export const SESSIONS_SHEET_URL = import.meta.env.VITE_SESSIONS_SHEET_URL || '';
