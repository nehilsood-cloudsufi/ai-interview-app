import httpx

from app.config import settings


def check():
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
