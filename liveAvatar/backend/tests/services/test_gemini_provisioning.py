import httpx
import pytest
import respx

from app.services import gemini_provisioning

BASE_URL = "https://api.liveavatar.com/v1"


@pytest.fixture(autouse=True)
def reset_gemini_globals():
    gemini_provisioning._gemini_secret_id = None
    gemini_provisioning._gemini_llm_configuration_id = None
    yield
    gemini_provisioning._gemini_secret_id = None
    gemini_provisioning._gemini_llm_configuration_id = None


@respx.mock
async def test_provision_noop_without_gemini_key(patch_settings):
    patch_settings(gemini_api_key=None, liveavatar_api_key="live-key")
    await gemini_provisioning.provision_gemini()
    assert gemini_provisioning.get_gemini_llm_configuration_id() is None
    assert len(respx.calls) == 0


@respx.mock
async def test_provision_noop_without_liveavatar_key(patch_settings):
    patch_settings(gemini_api_key="gem-key", liveavatar_api_key=None)
    await gemini_provisioning.provision_gemini()
    assert gemini_provisioning.get_gemini_llm_configuration_id() is None
    assert len(respx.calls) == 0


@respx.mock
async def test_provision_happy_path(patch_settings):
    patch_settings(
        gemini_api_key="gem-key",
        liveavatar_api_key="live-key",
        liveavatar_base_url=BASE_URL,
    )
    secret_route = respx.post(f"{BASE_URL}/secrets").mock(
        return_value=httpx.Response(200, json={"data": {"id": "secret-1"}})
    )
    llm_route = respx.post(f"{BASE_URL}/llm-configurations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "llm-1"}})
    )

    await gemini_provisioning.provision_gemini()

    assert secret_route.called
    assert llm_route.called
    assert gemini_provisioning.get_gemini_llm_configuration_id() == "llm-1"
    assert gemini_provisioning._gemini_secret_id == "secret-1"


@respx.mock
async def test_provision_uses_llm_configuration_id_fallback_key(patch_settings):
    patch_settings(
        gemini_api_key="gem-key",
        liveavatar_api_key="live-key",
        liveavatar_base_url=BASE_URL,
    )
    respx.post(f"{BASE_URL}/secrets").mock(
        return_value=httpx.Response(200, json={"data": {"id": "secret-1"}})
    )
    respx.post(f"{BASE_URL}/llm-configurations").mock(
        return_value=httpx.Response(
            200, json={"data": {"llm_configuration_id": "llm-fallback-1"}}
        )
    )

    await gemini_provisioning.provision_gemini()

    assert gemini_provisioning.get_gemini_llm_configuration_id() == "llm-fallback-1"


@respx.mock
async def test_provision_http_failure_resets_globals(patch_settings):
    patch_settings(
        gemini_api_key="gem-key",
        liveavatar_api_key="live-key",
        liveavatar_base_url=BASE_URL,
    )
    respx.post(f"{BASE_URL}/secrets").mock(return_value=httpx.Response(500))

    # Should not raise.
    await gemini_provisioning.provision_gemini()

    assert gemini_provisioning.get_gemini_llm_configuration_id() is None
    assert gemini_provisioning._gemini_secret_id is None


@respx.mock
async def test_provision_second_call_failure_resets_globals(patch_settings):
    patch_settings(
        gemini_api_key="gem-key",
        liveavatar_api_key="live-key",
        liveavatar_base_url=BASE_URL,
    )
    respx.post(f"{BASE_URL}/secrets").mock(
        return_value=httpx.Response(200, json={"data": {"id": "secret-1"}})
    )
    respx.post(f"{BASE_URL}/llm-configurations").mock(return_value=httpx.Response(500))

    await gemini_provisioning.provision_gemini()

    assert gemini_provisioning.get_gemini_llm_configuration_id() is None
    assert gemini_provisioning._gemini_secret_id is None


@respx.mock
async def test_provision_unexpected_exception_resets_globals(patch_settings):
    patch_settings(
        gemini_api_key="gem-key",
        liveavatar_api_key="live-key",
        liveavatar_base_url=BASE_URL,
    )
    # Malformed response body (missing "data" key) triggers a KeyError inside the
    # generic except Exception branch, rather than an HTTPStatusError.
    respx.post(f"{BASE_URL}/secrets").mock(return_value=httpx.Response(200, json={}))

    await gemini_provisioning.provision_gemini()

    assert gemini_provisioning.get_gemini_llm_configuration_id() is None
    assert gemini_provisioning._gemini_secret_id is None


@respx.mock
async def test_deprovision_noop_without_liveavatar_key(patch_settings):
    patch_settings(liveavatar_api_key=None)
    await gemini_provisioning.deprovision_gemini()
    assert len(respx.calls) == 0


@respx.mock
async def test_deprovision_calls_both_deletes(patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)
    gemini_provisioning._gemini_llm_configuration_id = "llm-1"
    gemini_provisioning._gemini_secret_id = "secret-1"

    llm_delete = respx.delete(f"{BASE_URL}/llm-configurations/llm-1").mock(
        return_value=httpx.Response(200)
    )
    secret_delete = respx.delete(f"{BASE_URL}/secrets/secret-1").mock(
        return_value=httpx.Response(200)
    )

    await gemini_provisioning.deprovision_gemini()

    assert llm_delete.called
    assert secret_delete.called


@respx.mock
async def test_deprovision_tries_secret_delete_even_if_llm_delete_fails(patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)
    gemini_provisioning._gemini_llm_configuration_id = "llm-1"
    gemini_provisioning._gemini_secret_id = "secret-1"

    llm_delete = respx.delete(f"{BASE_URL}/llm-configurations/llm-1").mock(
        side_effect=httpx.ConnectError("boom")
    )
    secret_delete = respx.delete(f"{BASE_URL}/secrets/secret-1").mock(
        return_value=httpx.Response(200)
    )

    # Should not raise even though the first delete raised.
    await gemini_provisioning.deprovision_gemini()

    assert llm_delete.called
    assert secret_delete.called


@respx.mock
async def test_deprovision_secret_delete_failure_does_not_raise(patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)
    gemini_provisioning._gemini_llm_configuration_id = "llm-1"
    gemini_provisioning._gemini_secret_id = "secret-1"

    llm_delete = respx.delete(f"{BASE_URL}/llm-configurations/llm-1").mock(
        return_value=httpx.Response(200)
    )
    secret_delete = respx.delete(f"{BASE_URL}/secrets/secret-1").mock(
        side_effect=httpx.ConnectError("boom")
    )

    # Should not raise even though the secret delete raised.
    await gemini_provisioning.deprovision_gemini()

    assert llm_delete.called
    assert secret_delete.called


@respx.mock
async def test_deprovision_noop_when_nothing_provisioned(patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)
    await gemini_provisioning.deprovision_gemini()
    assert len(respx.calls) == 0
