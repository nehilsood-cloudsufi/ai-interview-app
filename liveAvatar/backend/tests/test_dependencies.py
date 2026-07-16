from app.dependencies import resolve_api_key


def test_resolve_api_key_prefers_request_key(patch_settings):
    patch_settings(liveavatar_api_key="env-key")
    assert resolve_api_key("request-key") == "request-key"


def test_resolve_api_key_falls_back_to_settings(patch_settings):
    patch_settings(liveavatar_api_key="env-key")
    assert resolve_api_key(None) == "env-key"


def test_resolve_api_key_empty_string_falls_back(patch_settings):
    patch_settings(liveavatar_api_key="env-key")
    assert resolve_api_key("") == "env-key"


def test_resolve_api_key_none_when_neither_set(patch_settings):
    patch_settings(liveavatar_api_key=None)
    assert resolve_api_key(None) is None
