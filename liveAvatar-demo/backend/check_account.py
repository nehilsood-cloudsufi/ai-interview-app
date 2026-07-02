import os
import httpx
from dotenv import load_dotenv

load_dotenv()

LIVEAVATAR_API_KEY = os.getenv("LIVEAVATAR_API_KEY")
LIVEAVATAR_BASE_URL = "https://api.liveavatar.com/v1"

def check():
    with httpx.Client() as client:
        # Check credits
        res = client.get(
            f"{LIVEAVATAR_BASE_URL}/users/credits",
            headers={"X-API-KEY": LIVEAVATAR_API_KEY}
        )
        print("Credits:", res.status_code, res.text)
        
        # Check sessions
        res = client.get(
            f"{LIVEAVATAR_BASE_URL}/sessions?type=active",
            headers={"X-API-KEY": LIVEAVATAR_API_KEY}
        )
        print("Sessions:", res.status_code, res.text)
        
        data = res.json()
        if data.get("data") and data["data"]["results"]:
            session_id = data["data"]["results"][0]["id"]
            print(f"Trying to delete session {session_id}...")
            del_res = client.delete(
                f"{LIVEAVATAR_BASE_URL}/sessions/{session_id}",
                headers={"X-API-KEY": LIVEAVATAR_API_KEY}
            )
            print("Delete status:", del_res.status_code, del_res.text)

if __name__ == "__main__":
    check()
