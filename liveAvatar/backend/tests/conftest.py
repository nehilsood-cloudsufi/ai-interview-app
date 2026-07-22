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
    "app.routers.interview",
    "app.routers.llm_gateway",
    "app.routers.sessions",
    "app.services.interview_config",
    "app.services.liveavatar_client",
    "app.services.gemini_client",
    "app.services.host_agent",
    "app.services.evaluator_agent",
    "app.services.scout_agent",
    "app.services.transcript_store",
    "app.services.summary_service",
]


@pytest.fixture(autouse=True)
def _zero_utterance_settle(monkeypatch):
    """Zero the gateway's utterance-settle sleep for the whole suite - the
    real default (~1.2s per processed turn) would slow every gateway test to
    a crawl. Tests that verify the settle behavior itself opt back in via
    patch_settings(host_utterance_settle_seconds=...)."""
    fast = dataclasses.replace(_original_settings, host_utterance_settle_seconds=0.0)
    for mod_name in _SETTINGS_IMPORTERS:
        mod = importlib.import_module(mod_name)
        monkeypatch.setattr(mod, "settings", fast, raising=True)


@pytest.fixture
def patch_settings(monkeypatch):
    """Returns a helper that builds a new Settings instance (based on the
    real, process-start settings) with the given overrides applied, and
    monkeypatches it into every module that imported `settings` by value.
    Keeps the suite-wide zero utterance-settle (see _zero_utterance_settle)
    unless a test overrides it explicitly."""

    def _patch(**overrides) -> Settings:
        overrides.setdefault("host_utterance_settle_seconds", 0.0)
        new_settings = dataclasses.replace(_original_settings, **overrides)
        for mod_name in _SETTINGS_IMPORTERS:
            mod = importlib.import_module(mod_name)
            monkeypatch.setattr(mod, "settings", new_settings, raising=True)
        return new_settings

    return _patch


@pytest.fixture
def client():
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


@pytest.fixture(autouse=True)
def clear_questionnaire_caches():
    """Every lru_cache'd loader in interview_config needs its cache cleared
    around each test - otherwise a domain/questionnaire/rubric loaded (or
    monkeypatched away) in one test leaks into the next."""
    from app.services.interview_config import get_questionnaire, get_rubric, list_domains

    get_questionnaire.cache_clear()
    get_rubric.cache_clear()
    list_domains.cache_clear()
    yield
    get_questionnaire.cache_clear()
    get_rubric.cache_clear()
    list_domains.cache_clear()


@pytest.fixture
def sample_turns():
    from app.models import TranscriptTurn

    return [
        TranscriptTurn(role="interviewer", text="Tell me about RAG.", timestamp=1.0),
        TranscriptTurn(role="candidate", text="RAG combines retrieval with generation.", timestamp=2.0),
    ]
