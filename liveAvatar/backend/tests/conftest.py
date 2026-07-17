import dataclasses
import importlib

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.config import Settings
from app.config import settings as _original_settings
from app.main import app
from app.services import interview_state, session_state

# Every module (besides app.config itself) that did
# `from app.config import settings` at import time. A bound-reference import
# means patching `app.config.settings` alone will NOT propagate to these -
# each module's own `settings` name must be monkeypatched individually.
_SETTINGS_IMPORTERS = [
    "app.config",
    "app.main",
    "app.dependencies",
    "app.routers.resume",
    "app.routers.vendor",
    "app.services.resume_parser",
    "app.services.liveavatar_client",
    "app.services.gemini_provisioning",
    "app.services.transcript_store",
    "app.services.summary_service",
]


@pytest.fixture
def patch_settings(monkeypatch):
    """Returns a helper that builds a new Settings instance (based on the
    real, process-start settings) with the given overrides applied, and
    monkeypatches it into every module that imported `settings` by value."""

    def _patch(**overrides) -> Settings:
        new_settings = dataclasses.replace(_original_settings, **overrides)
        for mod_name in _SETTINGS_IMPORTERS:
            mod = importlib.import_module(mod_name)
            monkeypatch.setattr(mod, "settings", new_settings, raising=True)
        return new_settings

    return _patch


@pytest.fixture
def client():
    # Deliberately NOT `with TestClient(app) as client:` - entering the
    # context manager would trigger FastAPI's lifespan (provision/deprovision
    # Gemini), which makes real network calls if unmocked. Ordinary router
    # tests don't want that.
    return TestClient(app)


@pytest_asyncio.fixture
async def async_client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def reset_session_counter():
    session_state.active_sessions._count = 0
    yield
    session_state.active_sessions._count = 0


@pytest.fixture(autouse=True)
def reset_interview_state():
    interview_state._interviews.clear()
    yield
    interview_state._interviews.clear()


@pytest.fixture
def tmp_transcripts_dir(tmp_path, patch_settings):
    directory = tmp_path / "transcripts"
    patch_settings(transcripts_local_dir=str(directory), gcs_bucket=None)
    return directory


@pytest.fixture
def fake_gcs_client(monkeypatch):
    from google.cloud import storage

    from tests.fakes import FakeStorageClient

    fake_client = FakeStorageClient()
    monkeypatch.setattr(storage, "Client", lambda: fake_client)
    return fake_client


@pytest.fixture
def sample_turns():
    from app.models import TranscriptTurn

    return [
        TranscriptTurn(role="interviewer", text="Tell me about RAG.", timestamp=1.0),
        TranscriptTurn(role="candidate", text="RAG combines retrieval with generation.", timestamp=2.0),
    ]
