"""Tolerant JSON extraction for LLM responses.

Gemini's OpenAI-compatible endpoint occasionally decorates its JSON-mode
output: markdown code fences, leading prose, or trailing extra content
after a complete object (all observed live on 2026-07-18 — see
docs/llm-gateway-notes.md). `parse_llm_json` recovers the first JSON
object from such content instead of failing on anything that isn't a
bare object.
"""

import json

# How much of the offending content to embed in the error message; enough
# to diagnose from logs without dumping an entire completion.
_SNIPPET_LEN = 500

_FENCE = "```"


def parse_llm_json(content: str) -> dict:
    """Extract the first JSON object from an LLM completion.

    Handles markdown code fences, leading prose before the object, and
    trailing extra data after it. Raises ValueError (with a truncated
    snippet of the raw content) when no JSON object can be decoded.
    """
    text = content.strip()

    # Strip a fenced block (```json ... ``` or plain ``` ... ```) if the
    # object lives inside one.
    if _FENCE in text:
        start = text.find(_FENCE) + len(_FENCE)
        end = text.find(_FENCE, start)
        if end != -1:
            fenced = text[start:end]
            # Drop an optional language tag on the fence's first line.
            first_newline = fenced.find("\n")
            if first_newline != -1 and "{" not in fenced[:first_newline]:
                fenced = fenced[first_newline + 1 :]
            text = fenced.strip()

    brace = text.find("{")
    if brace == -1:
        raise ValueError(f"No JSON object in LLM content: {content[:_SNIPPET_LEN]!r}")

    try:
        parsed, _ = json.JSONDecoder().raw_decode(text[brace:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed JSON in LLM content ({exc}): {content[:_SNIPPET_LEN]!r}") from exc

    if not isinstance(parsed, dict):
        raise ValueError(f"LLM content is not a JSON object: {content[:_SNIPPET_LEN]!r}")
    return parsed
