"""In-memory count of active LiveAvatar sessions, used for the concurrency cap.

The count lives in process memory only, so it resets to zero on every backend
restart. Known drift: it is only decremented on an explicit `/api/session/stop`
(the user clicking stop, or the frontend's orphaned-session cleanup). When
LiveAvatar ends a session on its own - e.g. `MAX_DURATION_REACHED` - the
frontend's `SESSION_DISCONNECTED` handler resets local UI state but never calls
`/api/session/stop`, so the counter can creep upward across repeated sessions
until a restart. This is original, verified behavior, not an artifact of the
`app/` restructure."""


class SessionCounter:
    """A bare non-negative integer counter with increment / decrement / read.

    Not thread-safe or async-locked - it relies on the single-process, mostly
    cooperative FastAPI request handling and the fact that a small drift is
    tolerable for a soft concurrency cap."""

    def __init__(self) -> None:
        """Start the counter at zero."""
        self._count = 0

    @property
    def count(self) -> int:
        """The current number of sessions considered active."""
        return self._count

    def increment(self) -> None:
        """Record a newly started session."""
        self._count += 1

    def decrement(self) -> None:
        """Record a stopped session, clamping at zero so it never goes negative."""
        if self._count > 0:
            self._count -= 1


# Process-wide singleton the concurrency router reads and mutates.
active_sessions = SessionCounter()
