"""Shared request-time helpers for the routers.

Currently just `resolve_api_key`, the single place that reads the server's
LiveAvatar API key and turns a missing key into a clean HTTP error. The
sessions router calls it before every LiveAvatar API interaction.
"""

from fastapi import HTTPException

from app.config import settings


def resolve_api_key() -> str:
    """Single-tenant deployment: the server's own LIVEAVATAR_API_KEY is the
    only key path - no browser-supplied overrides."""
    if not settings.liveavatar_api_key:
        raise HTTPException(status_code=500, detail="LiveAvatar API Key not configured")
    return settings.liveavatar_api_key
