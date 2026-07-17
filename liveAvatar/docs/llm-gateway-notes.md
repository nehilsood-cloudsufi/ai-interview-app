# LLM Gateway Notes — Phase 0 Spike Findings

**Date:** 2026-07-17 · **Spike:** Task 0.1 of `docs/plans/2026-07-17-resonance-multi-agent-plan.md`
**Setup:** `app/routers/spike_llm_gateway.py` exposed via a `cloudflared` tunnel, registered as a LiveAvatar Custom LLM (`scripts/spike_llm_gateway_setup.py`), one live sandbox session with 5 conversational turns.

**Verdict: the backend-as-LLM architecture works.** The avatar spoke our hardcoded reply on every turn. No blockers found; Tasks A4/B1/B2 can proceed. Everything below is the observed contract they must match.

## Who calls us

- Caller is **LiveKit Agents 1.6.4 (Python)** using the **OpenAI Python SDK** (`x-stainless-*` headers, SDK 2.44.0), from HeyGen-operated AWS infra (`us-east-2`), not from the browser.
- Useful correlation headers on every request: `x-livekit-agent-id`, `x-livekit-job-id`, `x-livekit-room-id` (the room id is stable for the whole session).
- HeyGen accepted an arbitrary `trycloudflare.com` `base_url` — no allowlist at config-creation or call time.

## Auth

- Our secret's value arrives verbatim as **`Authorization: Bearer <secret_value>`** on every request. Per-interview `gateway_token` validation (Task B2) works exactly as planned.
- **Gotcha found:** the Secrets API has **no `LLM_API_KEY` type**. Valid types: `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, `GEMINI_API_KEY`, `FISH_API_KEY`, `CARTESIA_API_KEY`. Custom OpenAI-compatible endpoints use **`OPENAI_API_KEY`** (Task A4's `create_llm_secret` must use this).

## Request body (observed on all 5 turns)

Standard OpenAI chat-completions JSON:

```json
{
  "messages": [
    {"role": "system", "content": "<the LiveAvatar CONTEXT prompt, verbatim>"},
    {"role": "assistant", "content": "<the context's opening_text>"},
    {"role": "user", "content": "<ASR transcript of turn 1>"},
    {"role": "assistant", "content": "<our reply to turn 1>"},
    {"role": "user", "content": "<ASR transcript of latest turn>"}
  ],
  "model": "resonance-host",
  "stream": true,
  "stream_options": {"include_usage": true},
  "temperature": 0.5
}
```

Key facts:

1. **Full conversation history is resent every turn**, growing linearly. The latest user utterance is always the final message.
2. **The `system` message is the LiveAvatar *context* prompt** — the context we create in Task A4 is not just silent-avatar protection; its prompt text lands in our request. Keep it minimal/neutral (e.g. one line) since the real Host prompt is built server-side and the incoming system message will be ignored.
3. The context's `opening_text` greeting appears as the first `assistant` message.
4. `model` echoes the LLM configuration's `model_name` (`resonance-host`).
5. **`stream` is always `true`** (with `stream_options.include_usage: true`), `temperature: 0.5`. Non-streaming was never requested; B2 should still keep the non-stream branch for curl/debugging.

## Response requirements

- Our spike's SSE shape was accepted as-is: `data: {chat.completion.chunk with delta.content}` lines, a final chunk with `finish_reason: "stop"`, then `data: [DONE]`. Word-by-word chunks lip-synced fine.
- We did **not** send the `include_usage` usage chunk and nothing broke — optional, but harmless to add.

## Latency / timeout / retry budget

- **`x-stainless-read-timeout: 10.0`** — the OpenAI SDK on their side uses a **10-second read timeout**. The Host's full turn (auth + Gemini call + first streamed byte) must land well inside that; target first-token < ~3s.
- `x-stainless-retry-count: 0` on all observed requests; no retries or duplicate deliveries seen (all turns returned 200 quickly, so retry-on-5xx behavior remains unobserved — B2's never-500 canned-reply design makes this moot).
- Exactly **one request per user utterance**; no speculative or duplicate calls observed.

## Implications locked in for the implementation tasks

- **A4:** `create_llm_secret` → `secret_type: "OPENAI_API_KEY"`, value = the interview's `gateway_token`; context prompt should be one neutral line.
- **B1:** don't rebuild history from our own state for the Gemini call if not needed — HeyGen's `messages` already carry the whole conversation; our state's `turns` remain the source of truth for scoring/transcripts.
- **B2:** parse the *last* `user` message; always answer in SSE-chunk form (mirroring the shapes above); keep responses fast (<3s to first token) and never return 5xx.
- Bonus verified live: with `GEMINI_API_KEY` unset, transcript finalize soft-failed the summary and still saved the record — existing soft-fail semantics hold in gateway mode.
