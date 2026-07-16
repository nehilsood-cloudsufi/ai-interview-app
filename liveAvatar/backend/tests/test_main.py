from app.main import app, lifespan
from app.services import gemini_provisioning


def _all_paths(routes):
    """Recursively collect `.path` from a route list, descending into
    included routers (FastAPI's `_IncludedRouter` wraps a sub-router with an
    `original_router` attribute) and mounts (`.routes`)."""
    paths = set()
    for route in routes:
        path = getattr(route, "path", None)
        if path:
            paths.add(path)
        sub_router = getattr(route, "original_router", None)
        if sub_router is not None:
            paths |= _all_paths(sub_router.routes)
        elif hasattr(route, "routes"):
            paths |= _all_paths(route.routes)
    return paths


def test_all_expected_routes_registered():
    paths = _all_paths(app.routes)
    expected = {
        "/api/concurrency",
        "/api/upload-resume",
        "/api/session",
        "/api/session/stop",
        "/api/transcript/finalize",
        "/api/transcript/{session_id}",
    }
    assert expected.issubset(paths)


def test_cors_configured_to_allow_any_origin(client):
    # allow_origins=["*"] combined with allow_credentials=True makes
    # Starlette's CORSMiddleware reflect the requesting Origin (rather than
    # a literal "*", which isn't spec-legal alongside credentials) while
    # still allowing credentials for any origin.
    response = client.get(
        "/api/concurrency",
        headers={"Origin": "http://example.com"},
    )
    assert response.headers.get("access-control-allow-origin") == "http://example.com"
    assert response.headers.get("access-control-allow-credentials") == "true"


async def test_lifespan_calls_provision_then_deprovision(monkeypatch):
    calls = []

    async def fake_provision():
        calls.append("provision")

    async def fake_deprovision():
        calls.append("deprovision")

    monkeypatch.setattr(gemini_provisioning, "provision_gemini", fake_provision)
    monkeypatch.setattr(gemini_provisioning, "deprovision_gemini", fake_deprovision)

    async with lifespan(app):
        assert calls == ["provision"]

    assert calls == ["provision", "deprovision"]
