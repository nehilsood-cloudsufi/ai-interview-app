"""Manual end-to-end smoke test of the LiveAvatar session lifecycle.

Mints a FULL-mode session token for the sandbox avatar, starts the session,
then immediately stops it - a fast way to confirm the LiveAvatar API and
credentials work and that starting/stopping frees the concurrency slot cleanly.
Named `test_` but this is a standalone script, NOT a pytest test (pytest would
try to collect it - run it directly instead). Prints each step's status and
swallows errors so a failure is reported rather than raised.

Requires `LIVEAVATAR_API_KEY` in the environment (read via app.config.settings,
so a backend/.env works). Run from liveAvatar/backend:
    uv run python scripts/smoke_test_concurrency.py
"""

import httpx

from app.config import settings


def test():
    """Create a sandbox session token, start the session, then stop it,
    printing the HTTP status at each step. Any exception is caught and printed
    rather than propagated, so the script always finishes cleanly."""
    print("Testing session creation...")
    with httpx.Client() as client:
        try:
            token_payload = {
                "mode": "FULL",
                "avatar_id": settings.avatar_id,
                "is_sandbox": True,
                "avatar_persona": {"language": "en"},
            }
            res = client.post(
                f"{settings.liveavatar_base_url}/sessions/token",
                json=token_payload,
                headers={
                    "X-API-KEY": settings.liveavatar_api_key,
                    "Content-Type": "application/json",
                },
            )
            token_data = res.json()["data"]
            session_token = token_data["session_token"]

            print("Starting session...")
            start_res = client.post(
                f"{settings.liveavatar_base_url}/sessions/start",
                headers={"Authorization": f"Bearer {session_token}"},
            )
            print("Start Status:", start_res.status_code)

            print("Stopping session to free up concurrency...")
            stop_res = client.post(
                f"{settings.liveavatar_base_url}/sessions/stop",
                headers={"Authorization": f"Bearer {session_token}"},
            )
            print("Stop Status:", stop_res.status_code)
        except Exception as e:
            print("Error:", e)


if __name__ == "__main__":
    test()
