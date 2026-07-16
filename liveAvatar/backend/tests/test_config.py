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
    assert fresh.max_files == 5
    assert fresh.max_file_size_bytes == 5 * 1024 * 1024
    assert fresh.max_pdf_pages == 10
    assert fresh.transcripts_local_dir == "transcripts"
    assert fresh.gemini_model == "gemini-3.5-flash"


def test_settings_env_overrides(monkeypatch):
    monkeypatch.setenv("LIVEAVATAR_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("GCS_BUCKET", "my-bucket")
    fresh = Settings()
    assert fresh.liveavatar_api_key == "test-key"
    assert fresh.gemini_api_key == "gemini-key"
    assert fresh.gcs_bucket == "my-bucket"


def test_settings_prompt_content():
    assert "Topics Covered" in settings.interview_summary_prompt
    assert "AI Engineering role" in settings.interview_base_prompt
