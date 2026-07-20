import dataclasses

import pytest

from app.config import Settings, settings


def test_settings_is_frozen_dataclass():
    assert dataclasses.is_dataclass(settings)
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.avatar_id = "changed"


def test_settings_defaults():
    fresh = Settings(liveavatar_api_key=None, gemini_api_key=None, gcs_bucket=None)
    assert fresh.liveavatar_base_url == "https://api.liveavatar.com/v1"
    assert fresh.avatar_id == "dd73ea75-1218-4ef3-92ce-606d5f7fbc0a"
    assert fresh.transcripts_local_dir == "transcripts"
    assert fresh.gemini_model == "gemini-flash-latest"
    assert fresh.gemini_model_fallback == "gemini-3.5-flash"
    assert fresh.gemini_pro_model == "gemini-pro-latest"
    assert fresh.gemini_pro_model_fallback == "gemini-3.1-pro-preview"


def test_settings_env_overrides(monkeypatch):
    monkeypatch.setenv("LIVEAVATAR_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("GCS_BUCKET", "my-bucket")
    fresh = Settings()
    assert fresh.liveavatar_api_key == "test-key"
    assert fresh.gemini_api_key == "gemini-key"
    assert fresh.gcs_bucket == "my-bucket"


def test_host_streaming_disabled_by_default():
    fresh = Settings(liveavatar_api_key=None, gemini_api_key=None, gcs_bucket=None)
    assert fresh.host_streaming_enabled is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("True", True), ("1", True), ("false", False), ("0", False), ("", False)],
)
def test_host_streaming_env_override(monkeypatch, value, expected):
    monkeypatch.setenv("HOST_STREAMING_ENABLED", value)
    assert Settings().host_streaming_enabled is expected


def test_settings_prompt_content():
    assert "Topics Covered" in settings.interview_summary_prompt
