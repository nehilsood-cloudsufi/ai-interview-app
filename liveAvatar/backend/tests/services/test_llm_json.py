import pytest

from app.services.llm_json import parse_llm_json


def test_plain_object():
    assert parse_llm_json('{"reply": "hi", "answer_complete": true}') == {
        "reply": "hi",
        "answer_complete": True,
    }


def test_object_with_surrounding_whitespace():
    assert parse_llm_json('  \n{"a": 1}\n  ') == {"a": 1}


def test_fenced_json_block():
    content = '```json\n{"reply": "hello"}\n```'
    assert parse_llm_json(content) == {"reply": "hello"}


def test_fenced_block_without_language_tag():
    content = '```\n{"reply": "hello"}\n```'
    assert parse_llm_json(content) == {"reply": "hello"}


def test_leading_prose_before_object():
    content = 'Here is the JSON you asked for:\n{"reply": "hello"}'
    assert parse_llm_json(content) == {"reply": "hello"}


def test_trailing_extra_data_after_object():
    # The exact failure observed live: "Extra data: line 2 column 1".
    content = '{"reply": "hello", "answer_complete": true}\nSome trailing commentary.'
    assert parse_llm_json(content) == {"reply": "hello", "answer_complete": True}


def test_two_concatenated_objects_takes_first():
    content = '{"reply": "first"}\n{"reply": "second"}'
    assert parse_llm_json(content) == {"reply": "first"}


def test_nested_object_survives_trailing_data():
    content = '{"scores": {"a": 1, "b": 2}, "ok": true} extra'
    assert parse_llm_json(content) == {"scores": {"a": 1, "b": 2}, "ok": True}


def test_no_json_raises_with_snippet():
    with pytest.raises(ValueError, match="not json at all"):
        parse_llm_json("not json at all")


def test_broken_json_raises_with_snippet():
    with pytest.raises(ValueError, match="Malformed JSON"):
        parse_llm_json('{"reply": "unterminated')


def test_top_level_array_rejected():
    with pytest.raises(ValueError, match="No JSON object"):
        parse_llm_json('["not", "an", "object"]')


def test_error_snippet_is_truncated():
    long_garbage = "x" * 2000
    with pytest.raises(ValueError) as excinfo:
        parse_llm_json(long_garbage)
    assert len(str(excinfo.value)) < 700
