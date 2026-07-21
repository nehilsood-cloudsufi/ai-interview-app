import pytest
from fastapi import HTTPException

from app.dependencies import resolve_api_key


def test_resolve_api_key_returns_configured_key(patch_settings):
    patch_settings(liveavatar_api_key="env-key")
    assert resolve_api_key() == "env-key"


def test_resolve_api_key_raises_when_unset(patch_settings):
    patch_settings(liveavatar_api_key=None)
    with pytest.raises(HTTPException) as exc_info:
        resolve_api_key()
    assert exc_info.value.status_code == 500
