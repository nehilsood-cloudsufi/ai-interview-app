from fastapi import HTTPException

from app.config import settings


def resolve_api_key() -> str:
    """Single-tenant deployment: the server's own LIVEAVATAR_API_KEY is the
    only key path - no browser-supplied overrides."""
    if not settings.liveavatar_api_key:
        raise HTTPException(status_code=500, detail="LiveAvatar API Key not configured")
    return settings.liveavatar_api_key
