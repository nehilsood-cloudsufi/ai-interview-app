class SessionCounter:
    def __init__(self) -> None:
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def increment(self) -> None:
        self._count += 1

    def decrement(self) -> None:
        if self._count > 0:
            self._count -= 1


active_sessions = SessionCounter()
