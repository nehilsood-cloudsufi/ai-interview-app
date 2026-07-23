import httpx
import pytest
import respx

from app.models import TranscriptTurn
from app.services import summary_service

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


async def test_no_gemini_key_raises_without_http_call(patch_settings, sample_turns):
    patch_settings(gemini_api_key=None)
    with respx.mock:
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            await summary_service.generate_summary(sample_turns)
        assert len(respx.calls) == 0


async def test_empty_transcript_raises_without_http_call(patch_settings):
    patch_settings(gemini_api_key="gem-key")
    blank_turns = [TranscriptTurn(role="candidate", text="   ")]
    with respx.mock:
        with pytest.raises(ValueError, match="empty"):
            await summary_service.generate_summary(blank_turns)
        assert len(respx.calls) == 0


async def test_no_turns_at_all_raises_without_http_call(patch_settings):
    patch_settings(gemini_api_key="gem-key")
    with respx.mock:
        with pytest.raises(ValueError, match="empty"):
            await summary_service.generate_summary([])
        assert len(respx.calls) == 0


@respx.mock
async def test_happy_path_payload_shape(patch_settings, sample_turns):
    patch_settings(
        gemini_api_key="gem-key",
        gemini_base_url=GEMINI_BASE_URL,
        gemini_pro_model="gemini-pro-latest",
    )
    route = respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "  A tidy summary.  "}}]},
        )
    )

    result = await summary_service.generate_summary(sample_turns)

    assert result == "A tidy summary."
    assert route.called
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer gem-key"
    import json

    body = json.loads(request.content)
    assert body["model"] == "gemini-pro-latest"
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"
    assert "Interviewer: Tell me about RAG." in body["messages"][1]["content"]
    assert "Candidate: RAG combines retrieval with generation." in body["messages"][1]["content"]


@respx.mock
async def test_http_error_propagates(patch_settings, sample_turns):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        await summary_service.generate_summary(sample_turns)


@respx.mock
async def test_malformed_success_response_raises(patch_settings, sample_turns):
    # A 200 that doesn't match OpenAI's shape must raise (KeyError/IndexError),
    # not return garbage - the router's `except Exception` soft-fails it.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []})
    )

    with pytest.raises((KeyError, IndexError)):
        await summary_service.generate_summary(sample_turns)


@respx.mock
async def test_empty_completion_raises(patch_settings, sample_turns):
    # An empty (but 200 OK) completion must be treated as a failure, not a
    # silently "successful" empty summary - the router converts this raise
    # into summary_ok=False while still saving the transcript.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})
    )

    with pytest.raises(ValueError, match="empty completion"):
        await summary_service.generate_summary(sample_turns)


@respx.mock
async def test_whitespace_completion_raises(patch_settings, sample_turns):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "   \n  "}}]})
    )

    with pytest.raises(ValueError, match="empty completion"):
        await summary_service.generate_summary(sample_turns)


@respx.mock
async def test_null_content_raises(patch_settings, sample_turns):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": None}}]})
    )

    with pytest.raises(ValueError, match="empty completion"):
        await summary_service.generate_summary(sample_turns)


@respx.mock
async def test_blank_turns_are_filtered_out(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    turns = [
        TranscriptTurn(role="interviewer", text="  "),
        TranscriptTurn(role="candidate", text="Real answer"),
        TranscriptTurn(role="interviewer", text=""),
    ]
    route = respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    )

    await summary_service.generate_summary(turns)

    import json

    body = json.loads(route.calls[0].request.content)
    user_content = body["messages"][1]["content"]
    assert user_content == "Candidate: Real answer"

