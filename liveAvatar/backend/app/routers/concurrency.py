"""Read-only endpoint exposing the in-memory active-session counter.

The frontend polls this (via `useConcurrencyPoll`) to show how many avatar
sessions are currently live, so a second vendor can be warned before they
try to start one and hit LiveAvatar's account-wide concurrency limit. The
counter lives in `app.services.session_state` and is process-local: it only
decrements on an explicit `/api/session/stop`, so when LiveAvatar ends a
session on its own the count can drift upward until the backend restarts
(see the session_state note in CLAUDE.md). Same-origin UI endpoint - no auth.
"""

from fastapi import APIRouter

from app.models import ConcurrencyResponse
from app.services.session_state import active_sessions

router = APIRouter()


@router.get("/api/concurrency", response_model=ConcurrencyResponse)
async def get_concurrency():
    """Return the current number of active avatar sessions as a
    `ConcurrencyResponse` (`{"active_sessions": <int>}`). Takes no
    parameters and never errors - it just reports the process-local counter,
    which reflects sessions this backend instance started and has not yet
    seen an explicit stop for."""
    return {"active_sessions": active_sessions.count}
