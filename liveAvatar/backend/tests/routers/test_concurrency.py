from app.services.session_state import active_sessions


def test_concurrency_zero_initially(client):
    response = client.get("/api/concurrency")
    assert response.status_code == 200
    assert response.json() == {"active_sessions": 0}


def test_concurrency_reflects_increments(client):
    active_sessions.increment()
    active_sessions.increment()
    response = client.get("/api/concurrency")
    assert response.status_code == 200
    assert response.json() == {"active_sessions": 2}
