"""Tests for the incremental reply extractor used by the Host's streaming turn.

The extractor is fed Gemini content deltas (fragments of a JSON object whose
first field is the spoken `reply` string) and must emit the decoded characters
of that `reply` value as early as possible, regardless of how the deltas are
chopped up. `finalize()` then parses the whole buffered object for the routing
fields and returns any reply tail not yet emitted.
"""

import pytest

from app.services.reply_stream import ReplyStreamExtractor


def _feed_all(deltas: list[str]) -> tuple[str, ReplyStreamExtractor]:
    extractor = ReplyStreamExtractor()
    emitted = "".join(extractor.feed(delta) for delta in deltas)
    return emitted, extractor


def test_plain_reply_single_delta():
    whole = '{"reply": "Hello there", "answer_complete": true, "branch_signal": "default"}'
    emitted, extractor = _feed_all([whole])

    assert emitted == "Hello there"
    parsed, remaining = extractor.finalize()
    assert remaining == ""
    assert parsed["answer_complete"] is True
    assert parsed["branch_signal"] == "default"


def test_reply_streams_before_trailing_fields_arrive():
    # The whole point: reply characters come out before answer_complete exists.
    extractor = ReplyStreamExtractor()
    first = extractor.feed('{"reply": "Hi ')
    assert first == "Hi "
    rest = extractor.feed('friend", "answer_complete": true, "branch_signal": "default"}')
    assert rest == "friend"
    parsed, remaining = extractor.finalize()
    assert remaining == ""
    assert parsed["answer_complete"] is True


def test_split_across_many_deltas():
    whole = '{"reply": "Great, tell me more about your work.", "answer_complete": false, "branch_signal": "x"}'
    deltas = [whole[i : i + 3] for i in range(0, len(whole), 3)]
    emitted, extractor = _feed_all(deltas)

    assert emitted == "Great, tell me more about your work."
    parsed, remaining = extractor.finalize()
    assert remaining == ""
    assert parsed["answer_complete"] is False


def test_escaped_quote_in_reply():
    whole = '{"reply": "He said \\"hi\\" to me", "answer_complete": true, "branch_signal": "d"}'
    emitted, extractor = _feed_all([whole])

    assert emitted == 'He said "hi" to me'
    parsed, _ = extractor.finalize()
    assert parsed["reply"] == 'He said "hi" to me'


def test_escaped_backslash_and_newline():
    whole = '{"reply": "line1\\npath C:\\\\tmp", "answer_complete": true, "branch_signal": "d"}'
    emitted, extractor = _feed_all([whole])

    assert emitted == "line1\npath C:\\tmp"


def test_unicode_escape():
    whole = '{"reply": "caf\\u00e9 crawl", "answer_complete": true, "branch_signal": "d"}'
    emitted, extractor = _feed_all([whole])

    assert emitted == "café crawl"


def test_escape_split_across_delta_boundary():
    # Backslash arrives in one delta, the escaped char in the next.
    extractor = ReplyStreamExtractor()
    out = extractor.feed('{"reply": "quote: \\')
    out += extractor.feed('" end", "answer_complete": true, "branch_signal": "d"}')

    assert out == 'quote: " end'


def test_unicode_escape_split_across_delta_boundary():
    extractor = ReplyStreamExtractor()
    out = extractor.feed('{"reply": "x \\u00')
    out += extractor.feed('e9 y", "answer_complete": true, "branch_signal": "d"}')

    assert out == "x é y"


def test_reply_not_first_field_still_extracted():
    # Fallback ordering: routing fields precede reply. Correctness must hold;
    # a branch value even contains the word "reply" to catch naive matching.
    whole = (
        '{"branch_signal": "say reply now", "answer_complete": true, '
        '"reply": "The actual spoken line."}'
    )
    emitted, extractor = _feed_all([whole])

    parsed, remaining = extractor.finalize()
    assert emitted + remaining == "The actual spoken line."
    assert parsed["branch_signal"] == "say reply now"


def test_reply_not_first_split_across_tiny_deltas():
    # Forces _locate to re-run on partial buffers: keys, a boolean literal, and
    # a preceding string value all get chopped mid-token across deltas.
    whole = '{"answer_complete": true, "branch_signal": "moved on", "reply": "Final words."}'
    deltas = [whole[i : i + 4] for i in range(0, len(whole), 4)]
    emitted, extractor = _feed_all(deltas)

    parsed, remaining = extractor.finalize()
    assert emitted + remaining == "Final words."
    assert parsed["answer_complete"] is True
    assert parsed["branch_signal"] == "moved on"


def test_truncated_trailing_json_still_emits_full_reply():
    # Reply completed but the object is cut off mid trailing-field. The reply
    # was fully streamed; finalize signals the routing fields are unusable.
    extractor = ReplyStreamExtractor()
    emitted = extractor.feed('{"reply": "All good, moving on.", "answer_complete": tru')

    assert emitted == "All good, moving on."
    with pytest.raises(ValueError):
        extractor.finalize()
