import time

import httpx

from app.config import settings


def wait_for_cleanup():
    with httpx.Client() as client:
        while True:
            res = client.get(
                f"{settings.liveavatar_base_url}/sessions?type=active",
                headers={"X-API-KEY": settings.liveavatar_api_key},
            )
            data = res.json()
            results = data.get("data", {}).get("results", [])

            if not results:
                print("All sessions cleared! Ready to go.")
                break

            print(f"Still waiting... {len(results)} active session(s). Next check in 10s.")
            time.sleep(10)


if __name__ == "__main__":
    wait_for_cleanup()
