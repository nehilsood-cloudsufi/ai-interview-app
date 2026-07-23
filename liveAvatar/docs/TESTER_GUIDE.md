# Resonance — Tester Guide

*Deployed build under test: Cloud Run revision of 2026-07-23 (branch `dev`).*
*You need: Chrome (or any modern browser), a working microphone, ~45 minutes.*
*You do NOT need: any code, accounts, or tools — everything runs in the browser.*

## What you are testing

Resonance is an AI-run **vendor evaluation interview**. An avatar named **Noor**
interviews a vendor for ~5 minutes: she captures who you are conversationally
(no forms), walks through 7 fixed evaluation topics, and when the interview
ends the system produces a summary, a 7-category scorecard, and an
advance/clarify recommendation. There is also a text-chat fallback mode that
runs the same interview without video.

Your job: play the vendor, deliberately try the tricky moves listed below, and
record what actually happened.

## URLs and access

| Thing | Value |
|---|---|
| App (free tier) | https://liveavatar-demo-620046630330.us-central1.run.app/ |
| App (production tier) | https://liveavatar-demo-620046630330.us-central1.run.app/prod |
| Production passcode | Ask Nehil (nehil.sood@cloudsufi.com) — not written here on purpose |

Two tiers, same app:

- **Free tier (`/`)** — sandbox avatar, costs nothing, but HeyGen force-ends
  the session at **~1 minute**. Use it for smoke checks and to warm up. The
  1-minute cutoff is expected behavior, not a bug.
- **Production tier (`/prod`)** — real avatar, needs the passcode, burns
  ~2 credits/minute. You pick a session length (default 5 min). Use it for the
  full scripted runs. Please don't leave sessions running idle.

## Ground rules

- Speak naturally at normal pace. Noor phrases her questions differently every
  run — answer to the **topic**, don't hunt for exact wording.
- Keep the **Active Sessions** badge (top-right) in view — several checks use it.
- One session at a time.
- If the avatar freezes >20 seconds, a blue banner should appear; just speak
  again. If it's still stuck 30 s later, note the time and end the session —
  that's a bug report, not your fault.

---

## Test 1 — Smoke check (free tier, ~5 min total)

1. Open the free-tier URL. **Expect:** a start screen titled "Resonance" with a
   domain dropdown (default "Frontier Technology"), a Start Interview button,
   and a "Use text chat instead" button.
2. Click **Start Interview**, allow mic/camera. **Expect:** avatar video
   appears and Noor greets you within a few seconds; Active Sessions badge
   goes to 1.
3. Introduce yourself (name, role, company). **Expect:** the "Here's what I
   captured" card fills in with your details, and Noor reads them back for
   confirmation.
4. Let the ~1-minute sandbox cutoff happen (this is normal). **Expect:** the
   session ends cleanly, a summary panel appears, and within ~30 s a scorecard
   and recommendation fill in below it.

## Test 2 — End button + session counter (free tier, ~2 min)

1. Start another free session.
2. After Noor's greeting, click **End interview** yourself.
3. **Expect:** session stops immediately and the Active Sessions badge drops
   back to **0**. If it stays at 1, that's a bug — note it.

## Test 3 — Text-chat mode, full interview (free tier, ~10 min, no mic needed)

1. From the start screen click **Use text chat instead**.
2. Type your way through the whole interview. Terse one-line answers are fine
   in chat mode — she should accept them and move on, not nag for elaboration.
3. Mid-interview, click **Edit** on the details card and change one field
   (e.g. your role). **Expect:** your edit sticks for the rest of the
   interview, even if you later type something different.
4. Also mid-interview, ask her something off-topic ("what does RAG mean?").
   **Expect:** a one-line polite deferral, then she returns to her question —
   the interview does NOT advance to the next topic.
5. Click **End interview** after several topics. **Expect:** summary →
   scorecard → recommendation appear progressively; a **Download** button
   produces a Markdown file with the full transcript.

## Test 4 — The main event: scripted voice run (production tier, 5 min)

Enter the passcode at `/prod`, pick **5 minutes**, start, and follow this
script word-for-word. Each beat targets a specific past bug.

| Beat | You say | Pass criteria |
|---|---|---|
| 1 | "Hi Noor. My name is **[a wrong version of your name]**, an engineer at CloudSufi." → when she confirms: "Actually, it's **[your real name]** — then spell it out letter by letter." | Accepts the correction in one sentence, stays on topic, card shows the corrected name, never re-asks it |
| 2 | "It's a strategic priority for us… *(pause 2 s mid-thought)* …because our clients keep asking for frontier-tech solutions." | She waits through your pause; ONE reply to the whole thought; no question gets skipped |
| 3 | "We've built some AI things for clients." → after her probe: "We've shipped document-intelligence pipelines for Google and Swarovski, and we're building an agentic support system for Aramco." | She digs deeper on the thin answer instead of moving on; her follow-up relates to what she just asked |
| 4 | "Quick question — what does RAG actually mean?" | One-liner/deferral, then back to HER question; script does not advance |
| 5 | "Mostly third-party models, but we fine-tune our own LLMs in-house — proprietary orchestration on foundation APIs." | Acknowledgment + next topic in one reply |
| 6 | *(say nothing for ~25 s — skip this beat if past 3:30 on the timer)* | Blue "avatar may be stuck" banner appears ~20 s in; speaking again clears it |
| 7 | "Cloud-first, but we deploy on-prem or edge for data residency — we've done it for a bank." | Normal ack + advance |
| 8 | "Seventy percent of the team is engineering and R&D; the rest is delivery." | Normal ack + advance |
| 9 | "We start with services and move into co-innovation — open to joint IP for the right partner." | Normal ack + advance |
| 10 | Let her close; say only "Thanks, Noor." | Closing spoken ONCE (no repeat after your thanks); session stops itself ~8 s later; summary + scorecard appear; badge → 0 |

Whole-run pass criteria:

- All topics get asked — nothing silently skipped after your correction or
  off-topic question.
- Her wrap-up lands inside the final minute of the countdown — she is never
  cut off mid-sentence by the timer.
- Replies feel prompt (she starts speaking ~2–3 s after you stop). Long dead
  air (5 s+) on many turns is worth reporting even if nothing "breaks."
- Downloaded transcript shows all topics and your corrected name throughout.
- Scorecard shows **all 7 categories scored** (none marked "not covered").

## Test 5 — Edge personas (production tier, 2 × 5 min, optional)

- **The rambler:** answer every question with long, meandering stories.
  **Expect:** early on she probes deeper; in the last ~2 minutes she stops
  asking follow-ups and keeps replies short so all topics still fit.
- **The terse vendor:** answer everything in one short sentence. **Expect:**
  she follows up for substance, but still reaches her closing before time
  runs out.

## Test 6 — Access control (30 seconds)

- At `/prod`, enter a **wrong passcode** and try to start. **Expect:** a clean
  error message; no session starts, badge stays 0.

---

## How to report what you find

For every issue, capture:

1. **Timestamp** (your local time, as precise as you can — logs are matched by time).
2. **Tier and mode** (free/prod, voice/chat) and which test/beat you were on.
3. **What you said** (roughly) and **what Noor did** vs. what you expected.
4. The **downloaded transcript** (Download button on the summary panel), if
   the interview got that far.
5. A screenshot if it's visual (stuck banner, wrong card contents, broken layout).

Send everything to **Nehil (nehil.sood@cloudsufi.com)**. Backend logs are
matched to your timestamps on our side — the time matters more than anything
else.

### Known/expected behaviors (don't report these)

- Free-tier sessions dying at ~1 minute — HeyGen's sandbox limit.
- A short "settling" pause (~half a second) before Noor reacts — deliberate,
  so she doesn't talk over you mid-thought.
- Scorecard categories marked "not covered" on interviews that ended early —
  only a full run scores all 7.
- The domain dropdown existing at all — in real production an admin assigns
  it; the dropdown is a stand-in for testing.
