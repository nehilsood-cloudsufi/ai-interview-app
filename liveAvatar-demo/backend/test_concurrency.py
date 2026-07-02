import os
import httpx
from dotenv import load_dotenv

load_dotenv()

LIVEAVATAR_API_KEY = os.getenv("LIVEAVATAR_API_KEY")
LIVEAVATAR_BASE_URL = "https://api.liveavatar.com/v1"

def test():
    print("Testing session creation...")
    with httpx.Client() as client:
        try:
            token_payload = {
                "mode": "FULL",
                "avatar_id": "dd73ea75-1218-4ef3-92ce-606d5f7fbc0a",
                "is_sandbox": True,
                "avatar_persona": {
                    "language": "en"
                }
            }
            res = client.post(
                f"{LIVEAVATAR_BASE_URL}/sessions/token",
                json=token_payload,
                headers={
                    "X-API-KEY": LIVEAVATAR_API_KEY,
                    "Content-Type": "application/json"
                }
            )
            token_data = res.json()["data"]
            session_token = token_data["session_token"]
            
            print("Starting session...")
            start_res = client.post(
                f"{LIVEAVATAR_BASE_URL}/sessions/start",
                headers={
                    "Authorization": f"Bearer {session_token}"
                }
            )
            print("Start Status:", start_res.status_code)
            
            print("Stopping session to free up concurrency...")
            stop_res = client.post(
                f"{LIVEAVATAR_BASE_URL}/sessions/stop",
                headers={
                    "Authorization": f"Bearer {session_token}"
                }
            )
            print("Stop Status:", stop_res.status_code)
        except Exception as e:
            print("Error:", e)

if __name__ == "__main__":
    test()
