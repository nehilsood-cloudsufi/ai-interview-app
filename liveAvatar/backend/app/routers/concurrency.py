"""Read-only endpoint exposing the active-session count.

The frontend polls this (via `useConcurrencyPoll`) to show how many avatar
sessions are currently live, so a second vendor can be warned before they
try to start one and hit LiveAvatar's account-wide concurrency limit. The
count comes from the TTL-tracked registry in `app.services.session_state`:
it drops immediately on an explicit `/api/session/stop`, and a session
HeyGen ends on its own still falls out of the count once its tracked TTL
elapses, so it can no longer drift upward indefinitely. It is still
process-local and resets to zero on a backend restart. Same-origin UI
endpoint - no auth.
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
