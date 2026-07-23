from app.services.session_state import active_sessions


def test_concurrency_zero_initially(client):
    response = client.get("/api/concurrency")
    assert response.status_code == 200
    assert response.json() == {"active_sessions": 0}


def test_concurrency_reflects_tracked_sessions(client):
    active_sessions.track("tok-1", ttl_seconds=60)
    active_sessions.track("tok-2", ttl_seconds=60)
    response = client.get("/api/concurrency")
    assert response.status_code == 200
    assert response.json() == {"active_sessions": 2}
