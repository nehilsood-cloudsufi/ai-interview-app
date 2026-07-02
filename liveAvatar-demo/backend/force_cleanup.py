import os
import httpx
from dotenv import load_dotenv

load_dotenv()

LIVEAVATAR_API_KEY = os.getenv("LIVEAVATAR_API_KEY")
LIVEAVATAR_BASE_URL = "https://api.liveavatar.com/v1"

def force_stop_session(session_id: str):
    print(f"Force stopping session {session_id}...")
    with httpx.Client() as client:
        # Note: API might not support stop by session_id, usually it's stop by session_token.
        pass

# Since we don't have the session_token saved, we can't manually kill it easily if it's orphaned. 
# We just have to wait 1 min for sandbox to timeout.
