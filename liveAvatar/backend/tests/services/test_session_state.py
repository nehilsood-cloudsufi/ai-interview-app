from app.services.session_state import SessionTracker


def test_track_and_count():
    tracker = SessionTracker()
    tracker.track("tok-1", ttl_seconds=60)
    tracker.track("tok-2", ttl_seconds=60)
    assert tracker.count == 2


def test_release_is_idempotent():
    tracker = SessionTracker()
    tracker.track("tok-1", ttl_seconds=60)
    tracker.release("tok-1")
    tracker.release("tok-1")
    assert tracker.count == 0


def test_release_unknown_token_is_noop():
    tracker = SessionTracker()
    tracker.track("tok-1", ttl_seconds=60)
    tracker.release("nope")
    tracker.release(None)
    assert tracker.count == 1


def test_expired_sessions_fall_out_of_count():
    now = [0.0]
    tracker = SessionTracker(now_fn=lambda: now[0])
    tracker.track("tok-1", ttl_seconds=60)
    tracker.track("tok-2", ttl_seconds=120)
    assert tracker.count == 2

    now[0] = 90.0
    assert tracker.count == 1

    now[0] = 121.0
    assert tracker.count == 0


def test_retrack_same_token_refreshes_ttl():
    now = [0.0]
    tracker = SessionTracker(now_fn=lambda: now[0])
    tracker.track("tok-1", ttl_seconds=60)

    now[0] = 50.0
    tracker.track("tok-1", ttl_seconds=60)  # re-track before expiry refreshes the deadline

    now[0] = 90.0
    assert tracker.count == 1  # would have expired at 60 without the refresh

    now[0] = 111.0
    assert tracker.count == 0
