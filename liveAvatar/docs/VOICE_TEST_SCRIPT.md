# Voice Test Script — pre-deployment conversation check

A word-for-word script for a **5-minute `/prod` run** that exercises every
conversational failure mode fixed on 2026-07-22/23 (VAD fragmentation,
corrections, off-topic questions, pacing, stall recovery, clean ending).
Noor phrases questions differently each run — answer to the *topic*.
Keep the Active Sessions badge visible. On the intake form, enter **Neil**
as your name and CloudSufi as your company before starting.

| Beat | You say | Tests | Pass criteria |
|---|---|---|---|
| 1 | Noor greets you as "Neil" from the intake form. Reply: "Actually, it's **Nehil** — N-E-H-I-L." | correction handling | Accepts in one sentence, stays on topic, "Your details" card shows the corrected name, never re-asks |
| 2 | "It's a strategic priority for us… *(pause 2 s mid-thought)* …because our clients keep asking for frontier-tech solutions." | fragment survival | Waits through the pause; ONE reply to the whole thought; no skipped question |
| 3 | "We've built some AI things for clients." → after her probe: "We've shipped document-intelligence pipelines for Google and Swarovski, and we're building an agentic support system for Aramco." | time-generous follow-up | She digs deeper on the thin answer instead of moving on |
| 4 | "Quick question — what does RAG actually mean?" | off-topic deflection | One-liner/deferral, then back to HER question; script does not advance |
| 5 | "Mostly third-party models, but we fine-tune our own LLMs in-house — proprietary orchestration on foundation APIs." | normal turn | Ack + next topic in one reply |
| 6 | *(say nothing for ~25 s — skip if past 3:30)* | stall banner | Blue banner ~20 s in; answering resumes and clears it |
| 7 | "Cloud-first, but we deploy on-prem or edge for data residency — we've done it for a bank." | normal turn | — |
| 8 | "Seventy percent of the team is engineering and R&D; the rest is delivery." | normal turn | — |
| 9 | "We start with services and move into co-innovation — open to joint IP for the right partner." | normal turn | — |
| 10 | Let her close; say only "Thanks, Noor." | single closing + auto-stop | Closing spoken ONCE (no repeat after your thanks); session self-stops ~8 s later; summary + scorecard appear; badge → 0 |

**Afterwards:** download the transcript (all 7 topics present, corrected name
throughout); `uv run python scripts/cleanup_orphaned_resources.py` → 0 deletions.
Log check: `Gateway turn` lines advance nodes strictly in script order;
`superseded` lines are normal.
