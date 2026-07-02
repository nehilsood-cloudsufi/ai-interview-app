import os
import time
import httpx
from dotenv import load_dotenv

load_dotenv()

LIVEAVATAR_API_KEY = os.getenv("LIVEAVATAR_API_KEY")
LIVEAVATAR_BASE_URL = "https://api.liveavatar.com/v1"

def wait_for_cleanup():
    with httpx.Client() as client:
        while True:
            res = client.get(
                f"{LIVEAVATAR_BASE_URL}/sessions?type=active",
                headers={"X-API-KEY": LIVEAVATAR_API_KEY}
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
