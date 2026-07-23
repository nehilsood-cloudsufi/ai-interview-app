"""Quick manual inspection (and unstick) of the LiveAvatar account.

Prints the account's remaining credits and its currently-active sessions,
then deletes the first active session it finds - handy when a stuck/orphaned
session is holding a concurrency slot and blocking new ones. This is a one-off
operational script, not part of the served app and not run by the test suite.

Requires `LIVEAVATAR_API_KEY` in the environment (read via app.config.settings,
so a backend/.env works). Run from liveAvatar/backend:
    uv run python scripts/check_account.py
"""

import httpx

from app.config import settings


def check():
    """Fetch and print the account's credits and active sessions, then delete
    the first active session (if any) to free its concurrency slot. Prints the
    HTTP status and body of each call so the outcome is visible in the
    terminal."""
    with httpx.Client() as client:
        # Check credits
        res = client.get(
            f"{settings.liveavatar_base_url}/users/credits",
            headers={"X-API-KEY": settings.liveavatar_api_key},
        )
        print("Credits:", res.status_code, res.text)

        # Check sessions
        res = client.get(
            f"{settings.liveavatar_base_url}/sessions?type=active",
            headers={"X-API-KEY": settings.liveavatar_api_key},
        )
        print("Sessions:", res.status_code, res.text)

        data = res.json()
        if data.get("data") and data["data"]["results"]:
            session_id = data["data"]["results"][0]["id"]
            print(f"Trying to delete session {session_id}...")
            del_res = client.delete(
                f"{settings.liveavatar_base_url}/sessions/{session_id}",
                headers={"X-API-KEY": settings.liveavatar_api_key},
            )
            print("Delete status:", del_res.status_code, del_res.text)


if __name__ == "__main__":
    check()
