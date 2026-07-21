"""Incremental extraction of the Host's spoken `reply` from a streamed Gemini
turn.

The Host asks Gemini for a JSON object `{"reply", "answer_complete"}` with
`reply` declared first, so under the strict schema the `reply` string is the
first value emitted. Streaming lets the avatar start speaking as soon as those
characters arrive, but they land wrapped in JSON and chopped at arbitrary byte
boundaries. `ReplyStreamExtractor` is fed the content deltas and returns the
decoded characters of the `reply` value as soon as they are unambiguously
available, buffering the raw text so `finalize()` can parse the trailing
`answer_complete` field once the object is complete.

The extractor never advances past a fragment it cannot fully decode (a partial
`\\uXXXX` escape at a delta boundary, say) - it simply waits for the next feed.
If the object turns out malformed after the reply, `finalize()` raises, but the
reply characters have already been emitted correctly: the caller speaks them
and leaves state unchanged, matching the non-streaming soft-fail.
"""

from app.services.llm_json import parse_llm_json

_WS = " \t\r\n"
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def _scan_string(raw: str, start: int) -> tuple[str | None, int]:
    """Decode a JSON string beginning at ``raw[start]`` (which must be a `"`).

    Returns ``(value, index_after_closing_quote)``, or ``(None, start)`` if the
    string is not yet complete in ``raw`` (including a dangling escape)."""
    n = len(raw)
    i = start + 1  # past the opening quote
    out: list[str] = []
    while i < n:
        c = raw[i]
        if c == '"':
            return "".join(out), i + 1
        if c == "\\":
            if i + 1 >= n:
                return None, start  # escape char not yet arrived
            e = raw[i + 1]
            if e == "u":
                if i + 6 > n:
                    return None, start  # need the full \uXXXX
                out.append(chr(int(raw[i + 2 : i + 6], 16)))
                i += 6
            else:
                out.append(_ESCAPES.get(e, e))
                i += 2
        else:
            out.append(c)
            i += 1
    return None, start  # no closing quote yet


def _skip_value(raw: str, i: int) -> int | None:
    """Return the index just past the JSON value at ``raw[i]``, or None if the
    value is not yet fully present. Handles the value types the host schema can
    place before `reply`: strings and booleans (plus null/number for safety)."""
    n = len(raw)
    c = raw[i]
    if c == '"':
        _, end = _scan_string(raw, i)
        return None if end == i else end
    for literal in ("true", "false", "null"):
        if raw.startswith(literal, i):
            return i + len(literal)
        if literal.startswith(raw[i:]):  # partial literal at buffer end
            return None
    # Number (defensive; the schema has none): consume until a delimiter, but
    # only once we can see the delimiter so we don't stop mid-number.
    j = i
    while j < n and raw[j] in "+-0123456789.eE":
        j += 1
    if j == i:
        return None
    return None if j >= n else j


class ReplyStreamExtractor:
    def __init__(self) -> None:
        self._raw: list[str] = []
        self._buf = ""
        self._value_start: int | None = None  # index inside the reply string
        self._cursor = 0  # next undecoded index once value_start is known
        self._done = False  # reply string closed
        self._emitted = ""

    @property
    def emitted(self) -> str:
        """Reply characters already returned by feed()."""
        return self._emitted

    def feed(self, delta: str) -> str:
        """Consume one content delta; return any newly decoded reply chars."""
        if not delta:
            return ""
        self._raw.append(delta)
        self._buf += delta
        if self._done:
            return ""
        if self._value_start is None:
            self._value_start = self._locate()
            if self._value_start is None:
                return ""
            self._cursor = self._value_start
        chunk = self._decode()
        self._emitted += chunk
        return chunk

    def finalize(self) -> tuple[dict, str]:
        """Parse the complete buffered object and return ``(parsed, remaining)``
        where ``remaining`` is any reply tail not already returned by feed()
        (normally ``""``). Raises ValueError if the buffer is not valid JSON."""
        parsed = parse_llm_json(self._buf)
        full_reply = str(parsed.get("reply", ""))
        remaining = full_reply[len(self._emitted) :] if full_reply.startswith(self._emitted) else ""
        return parsed, remaining

    def _locate(self) -> int | None:
        """Find the index just inside the opening quote of the reply value, or
        None if the buffer does not yet reveal it."""
        raw = self._buf
        n = len(raw)
        i = raw.find("{")
        if i == -1:
            return None
        i += 1
        while True:
            while i < n and raw[i] in _WS + ",":
                i += 1
            if i >= n or raw[i] == "}":
                return None
            if raw[i] != '"':
                return None
            key, i = _scan_string(raw, i)
            if key is None:
                return None
            while i < n and raw[i] in _WS:
                i += 1
            if i >= n or raw[i] != ":":
                return None
            i += 1
            while i < n and raw[i] in _WS:
                i += 1
            if i >= n:
                return None
            if key == "reply":
                return i + 1 if raw[i] == '"' else None
            nxt = _skip_value(raw, i)
            if nxt is None:
                return None
            i = nxt

    def _decode(self) -> str:
        """Decode reply chars from the cursor as far as the buffer allows."""
        raw = self._buf
        n = len(raw)
        i = self._cursor
        out: list[str] = []
        while i < n:
            c = raw[i]
            if c == '"':
                self._done = True
                i += 1
                break
            if c == "\\":
                if i + 1 >= n:
                    break
                e = raw[i + 1]
                if e == "u":
                    if i + 6 > n:
                        break
                    out.append(chr(int(raw[i + 2 : i + 6], 16)))
                    i += 6
                else:
                    out.append(_ESCAPES.get(e, e))
                    i += 2
            else:
                out.append(c)
                i += 1
        self._cursor = i
        return "".join(out)
