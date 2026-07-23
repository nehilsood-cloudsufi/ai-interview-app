"""TTL-tracked count of active LiveAvatar sessions, used for the concurrency cap.

Historically this was a bare increment/decrement counter that only ever
decremented on an explicit `/api/session/stop` (the user clicking stop, or
the frontend's orphaned-session cleanup). When LiveAvatar ended a session on
its own - e.g. the dev tier's ~1-minute sandbox cap, surfaced to the SDK as
`SESSION_DISCONNECTED` - nothing ever decremented, so the count crept upward
across repeated sessions until a restart. `SessionTracker` below replaces
that: every tracked session carries an expiry deadline (set from the same
duration HeyGen was given for the stream), so a session that never gets an
explicit release still falls out of `count` on its own once the TTL elapses.
The count still resets to zero on a backend restart, same as before - only
the drift-while-running is fixed."""

import time


class SessionTracker:
    """Tracks active sessions by session_token with an expiry deadline, so a
    session HeyGen ends on its own falls out of the count by itself.

    Not thread-safe or async-locked - it relies on the single-process, mostly
    cooperative FastAPI request handling and the fact that a small drift is
    tolerable for a soft concurrency cap."""

    def __init__(self, now_fn=time.monotonic) -> None:
        """Start with no tracked sessions. `now_fn` is injected so tests can
        control the clock without sleeping."""
        self._now_fn = now_fn
        self._deadlines: dict[str, float] = {}

    def track(self, session_token: str, ttl_seconds: float) -> None:
        """Record session_token as active until ttl_seconds from now.
        Re-tracking an already-tracked token refreshes its deadline rather
        than stacking a second entry."""
        self._deadlines[session_token] = self._now_fn() + ttl_seconds

    def release(self, session_token: str | None) -> None:
        """Stop tracking session_token, e.g. on an explicit stop. Idempotent
        and safe on a token that was never tracked - `None`/unknown is a
        no-op rather than an error."""
        if session_token is None:
            return
        self._deadlines.pop(session_token, None)

    @property
    def count(self) -> int:
        """The number of sessions still within their TTL. Prunes expired
        entries as a side effect, so a session HeyGen ended on its own
        without an explicit release still drops out of the count once its
        deadline passes."""
        now = self._now_fn()
        expired = [token for token, deadline in self._deadlines.items() if deadline <= now]
        for token in expired:
            del self._deadlines[token]
        return len(self._deadlines)


# Process-wide singleton the sessions/concurrency routers read and mutate.
active_sessions = SessionTracker()
