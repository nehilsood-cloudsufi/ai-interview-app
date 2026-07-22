"""One-off cleanup of orphaned per-interview LiveAvatar resources.

Auto-ended sessions (sandbox ~1-min cap, prod max_session_duration) never
trigger /api/session/stop, so their per-interview LLM configs, gateway
secrets, and contexts accumulate on the HeyGen account. This deletes only
the auto-generated ones:
  - llm-configurations named  "Resonance Host <hex>"
  - secrets named             "Resonance Gateway <hex>"
  - contexts named            "AI Interviewer w/ Context <hex>"
Everything else (dashboard-created contexts like Noor, the voice agent,
embeds, other configs) is left untouched.

Run from liveAvatar/backend:  uv run python scripts/cleanup_orphaned_resources.py
"""

import json
import os
import urllib.request

from dotenv import load_dotenv

load_dotenv()

BASE = "https://api.liveavatar.com/v1"
KEY = os.environ["LIVEAVATAR_API_KEY"]


def req(method: str, path: str) -> dict:
    """Make one authenticated LiveAvatar API call and return the parsed JSON
    body (or `{}` for an empty body). Best-effort by design: any error is
    caught and returned as `{"error": <message>}` rather than raised, so a
    single failed delete never aborts the whole cleanup sweep."""
    r = urllib.request.Request(f"{BASE}{path}", method=method, headers={"X-API-KEY": KEY})
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return json.loads(resp.read() or b"{}")
    except Exception as e:  # noqa: BLE001 - best-effort cleanup, report and continue
        return {"error": str(e)}


def main() -> None:
    """Run the full cleanup sweep: delete the auto-generated "Resonance Host"
    LLM configurations, "Resonance Gateway" secrets, and "AI Interviewer w/
    Context" contexts (contexts are paged through until none remain), printing
    a count for each category and the names of any contexts left behind.
    Only the auto-named Resonance resources are matched; everything else on the
    account is left untouched."""
    cfgs = req("GET", "/llm-configurations").get("data", [])
    kill_cfgs = [c for c in cfgs if c["display_name"].startswith("Resonance Host ")]
    for c in kill_cfgs:
        req("DELETE", f"/llm-configurations/{c['id']}")
    print(f"deleted {len(kill_cfgs)} 'Resonance Host' llm-configurations")

    secrets = req("GET", "/secrets").get("data", [])
    kill_secrets = [s for s in secrets if s["secret_name"].startswith("Resonance Gateway ")]
    for s in kill_secrets:
        req("DELETE", f"/secrets/{s['id']}")
    print(f"deleted {len(kill_secrets)} 'Resonance Gateway' secrets")

    deleted = 0
    while True:
        page = req("GET", "/contexts?page_size=50").get("data", {}).get("results", [])
        targets = [c for c in page if c["name"].startswith("AI Interviewer w/ Context ")]
        if not targets:
            break
        for c in targets:
            req("DELETE", f"/contexts/{c['id']}")
            deleted += 1
    print(f"deleted {deleted} auto-generated contexts")

    remaining = req("GET", "/contexts?page_size=50").get("data", {})
    names = [c["name"] for c in remaining.get("results", [])][:10]
    print(f"contexts remaining: {remaining.get('count')} -> {names}")


if __name__ == "__main__":
    main()
