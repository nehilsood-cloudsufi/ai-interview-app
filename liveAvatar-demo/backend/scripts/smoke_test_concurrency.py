import httpx

from app.config import settings


def test():
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
