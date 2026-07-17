"""SPIKE — Phase 0 of the Resonance multi-agent plan.

Registers app/routers/spike_llm_gateway.py as a Custom LLM with LiveAvatar
so we can observe exactly how HeyGen calls it. Not part of the served app;
one-off, run by hand. Delete alongside the spike router after Task 0.1.

Usage:
    uv run python scripts/spike_llm_gateway_setup.py <tunnel_url>
    uv run python scripts/spike_llm_gateway_setup.py --teardown <secret_id> <llm_config_id>

<tunnel_url> is the public base URL exposing this backend, e.g. the
cloudflared URL (https://xyz.trycloudflare.com) or a Cloud Run dev revision.
"""

import sys

import httpx

from app.config import settings


def setup(tunnel_url: str) -> None:
    if not settings.liveavatar_api_key:
        print("Error: Missing LIVEAVATAR_API_KEY in .env")
        sys.exit(1)

    base_url = tunnel_url.rstrip("/") + "/llm/spike/v1"
    print(f"1. Storing a throwaway secret (the token HeyGen must send us back)...")
    with httpx.Client() as client:
        secret_res = client.post(
            f"{settings.liveavatar_base_url}/secrets",
            json={
                "secret_type": "LLM_API_KEY",
                "secret_value": "spike-not-a-real-key",
                "secret_name": "Resonance Gateway Spike Key",
            },
            headers={"X-API-KEY": settings.liveavatar_api_key},
        )
        secret_res.raise_for_status()
        secret_id = secret_res.json()["data"]["id"]
        print(f"   Secret created: {secret_id}")

        print(f"2. Creating LLM configuration pointing at {base_url} ...")
        llm_res = client.post(
            f"{settings.liveavatar_base_url}/llm-configurations",
            json={
                "display_name": "Resonance Gateway Spike",
                "model_name": "resonance-host",
                "secret_id": secret_id,
                "base_url": base_url,
            },
            headers={"X-API-KEY": settings.liveavatar_api_key},
        )
        llm_res.raise_for_status()
        llm_config_id = llm_res.json()["data"]["id"]
        print(f"   LLM configuration created: {llm_config_id}")

    print("\n========================================")
    print("SETUP COMPLETE.")
    print("1. Temporarily unset GEMINI_API_KEY when starting the backend, so")
    print("   auto-provisioning doesn't override this config:")
    print("     GEMINI_API_KEY= uv run uvicorn app.main:app --port 3001 --reload")
    print("2. Point the frontend at this config for one test session:")
    print(f"     VITE_LLM_CONFIG_ID={llm_config_id} npm run dev")
    print("3. After the test, tear down with:")
    print(f"     uv run python scripts/spike_llm_gateway_setup.py --teardown {secret_id} {llm_config_id}")
    print("========================================\n")


def teardown(secret_id: str, llm_config_id: str) -> None:
    if not settings.liveavatar_api_key:
        print("Error: Missing LIVEAVATAR_API_KEY in .env")
        sys.exit(1)

    with httpx.Client() as client:
        print(f"Deleting LLM configuration {llm_config_id}...")
        client.delete(
            f"{settings.liveavatar_base_url}/llm-configurations/{llm_config_id}",
            headers={"X-API-KEY": settings.liveavatar_api_key},
        )
        print(f"Deleting secret {secret_id}...")
        client.delete(
            f"{settings.liveavatar_base_url}/secrets/{secret_id}",
            headers={"X-API-KEY": settings.liveavatar_api_key},
        )
    print("Teardown complete.")


def main() -> None:
    if len(sys.argv) == 2:
        setup(sys.argv[1])
    elif len(sys.argv) == 4 and sys.argv[1] == "--teardown":
        teardown(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
