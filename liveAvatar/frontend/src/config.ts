export const API_URL = import.meta.env.PROD ? '' : (import.meta.env.VITE_API_URL || 'http://localhost:3001');

// These fallbacks exist because .env is deliberately excluded from the deployed
// container image — without them the avatar connects but stays silent.
export const DEFAULT_CONTEXT_ID = import.meta.env.VITE_CONTEXT_ID || 'ab9f75be-5932-4878-a577-d80d9deb038f';
export const DEFAULT_LLM_CONFIG_ID = import.meta.env.VITE_LLM_CONFIG_ID || '72090d75-09c7-4c6a-a3c5-809862722f7d';
