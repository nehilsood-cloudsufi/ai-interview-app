import asyncio
import json

import httpx
import respx

from app.config import settings
from app.routers import llm_gateway
from app.services import appraiser_agent, host_agent, interview_state
from app.services.host_agent import TurnResult
from app.services.interview_config import Branch, QuestionNode
from app.services.interview_state import VendorProfile

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
CHAT_URL = f"{GEMINI_BASE_URL}chat/completions"


def _seed_interview():
    return interview_state.create(
        VendorProfile(
            company_name="Acme Corp",
            website="https://acme.example",
            contact_name="Jane Doe",
            contact_role="CTO",
        )
    )


def _url(interview_id: str) -> str:
    return f"/llm/{interview_id}/v1/chat/completions"


def _auth(state) -> dict:
    return {"Authorization": f"Bearer {state.gateway_token}"}


def _openai_body(user_text: str | None = "We build ML pipelines.", stream: bool = False) -> dict:
    # Mirrors the observed HeyGen request (docs/llm-gateway-notes.md): full
    # history resent every turn, latest user utterance last. user_text=None
    # models HeyGen probing before any user utterance exists.
    messages = [
        {"role": "system", "content": "context prompt"},
        {"role": "assistant", "content": "opening greeting"},
    ]
    if user_text is not None:
        messages += [
            {"role": "user", "content": "an earlier utterance"},
            {"role": "assistant", "content": "an earlier reply"},
            {"role": "user", "content": user_text},
        ]
    return {"model": "resonance-host", "stream": stream, "messages": messages}


def _fake_handle_turn(monkeypatch, result: TurnResult):
    calls = []

    async def fake(state, user_text, questionnaire, rubric):
        calls.append({"state": state, "user_text": user_text, "questionnaire": questionnaire, "rubric": rubric})
        return result

    monkeypatch.setattr(host_agent, "handle_turn", fake)
    return calls


def _turn_result(reply="Great, tell me more.", answer_complete=False, completed_question=None, answer_text=""):
    return TurnResult(
        reply=reply,
        answer_complete=answer_complete,
        completed_question=completed_question,
        answer_text=answer_text,
    )


def _parse_sse(text: str) -> list[str]:
    return [block[len("data: ") :] for block in text.split("\n\n") if block.startswith("data: ")]


# --- auth / lookup -----------------------------------------------------------


def test_unknown_interview_404(client):
    response = client.post(_url("nope"), json=_openai_body(), headers={"Authorization": "Bearer whatever"})
    assert response.status_code == 404


def test_missing_auth_header_401(client):
    state = _seed_interview()
    response = client.post(_url(state.interview_id), json=_openai_body())
    assert response.status_code == 401


def test_wrong_token_401(client):
    state = _seed_interview()
    response = client.post(
        _url(state.interview_id), json=_openai_body(), headers={"Authorization": "Bearer wrong-token"}
    )
    assert response.status_code == 401


def test_non_bearer_scheme_401(client):
    state = _seed_interview()
    response = client.post(
        _url(state.interview_id),
        json=_openai_body(),
        headers={"Authorization": f"Basic {state.gateway_token}"},
    )
    assert response.status_code == 401


# --- happy paths -------------------------------------------------------------


def test_non_stream_happy_path(client, monkeypatch):
    state = _seed_interview()
    calls = _fake_handle_turn(monkeypatch, _turn_result(reply="Great, tell me more."))

    response = client.post(_url(state.interview_id), json=_openai_body(stream=False), headers=_auth(state))

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "resonance-host"
    assert body["id"].startswith("chatcmpl-")
    assert isinstance(body["created"], int)
    assert body["choices"] == [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Great, tell me more."},
            "finish_reason": "stop",
        }
    ]

    # handle_turn got the seeded state, the LAST user message, and the
    # shipped questionnaire/rubric singletons.
    assert len(calls) == 1
    assert calls[0]["state"] is state
    assert calls[0]["user_text"] == "We build ML pipelines."
    assert "verify_identity" in calls[0]["questionnaire"]
    assert calls[0]["rubric"]


def test_stream_happy_path(client, monkeypatch):
    state = _seed_interview()
    _fake_handle_turn(monkeypatch, _turn_result(reply="Great, tell me more."))

    response = client.post(_url(state.interview_id), json=_openai_body(stream=True), headers=_auth(state))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    frames = _parse_sse(response.text)
    assert frames[-1] == "[DONE]"
    chunks = [json.loads(frame) for frame in frames[:-1]]
    assert all(chunk["object"] == "chat.completion.chunk" for chunk in chunks)
    assert all(chunk["model"] == "resonance-host" for chunk in chunks)
    content = "".join(chunk["choices"][0]["delta"].get("content", "") for chunk in chunks)
    assert content == "Great, tell me more."
    assert chunks[-1]["choices"][0]["delta"] == {}
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert all(chunk["choices"][0]["finish_reason"] is None for chunk in chunks[:-1])


def test_no_user_message_returns_greeting_without_calling_host(client, monkeypatch):
    state = _seed_interview()
    calls = _fake_handle_turn(monkeypatch, _turn_result())

    response = client.post(_url(state.interview_id), json=_openai_body(user_text=None), headers=_auth(state))

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == llm_gateway._GREETING_REPLY
    assert calls == []


# --- never-fail rule ---------------------------------------------------------


def test_handle_turn_exception_returns_canned_reply_non_stream(client, monkeypatch):
    state = _seed_interview()

    async def boom(*args, **kwargs):
        raise RuntimeError("host exploded")

    monkeypatch.setattr(host_agent, "handle_turn", boom)

    response = client.post(_url(state.interview_id), json=_openai_body(stream=False), headers=_auth(state))

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == settings.host_fallback_reply


def test_handle_turn_exception_returns_canned_reply_stream(client, monkeypatch):
    state = _seed_interview()

    async def boom(*args, **kwargs):
        raise RuntimeError("host exploded")

    monkeypatch.setattr(host_agent, "handle_turn", boom)

    response = client.post(_url(state.interview_id), json=_openai_body(stream=True), headers=_auth(state))

    assert response.status_code == 200
    frames = _parse_sse(response.text)
    assert frames[-1] == "[DONE]"
    content = "".join(json.loads(f)["choices"][0]["delta"].get("content", "") for f in frames[:-1])
    assert content == settings.host_fallback_reply


def test_config_load_failure_returns_canned_reply(client, monkeypatch):
    state = _seed_interview()

    def boom():
        raise RuntimeError("bad questionnaire")

    monkeypatch.setattr(llm_gateway, "get_questionnaire", boom)

    response = client.post(_url(state.interview_id), json=_openai_body(stream=False), headers=_auth(state))

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == settings.host_fallback_reply


def test_malformed_body_returns_canned_reply_as_stream(client):
    # Unparsable body: can't know the requested format, default to SSE
    # (HeyGen always streams).
    state = _seed_interview()

    response = client.post(_url(state.interview_id), content=b"not json at all", headers=_auth(state))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _parse_sse(response.text)
    assert frames[-1] == "[DONE]"
    content = "".join(json.loads(f)["choices"][0]["delta"].get("content", "") for f in frames[:-1])
    assert content == settings.host_fallback_reply


# --- no mid-interview scoring ------------------------------------------------


def _completed_node() -> QuestionNode:
    return QuestionNode(
        id="company_overview",
        topic="company_overview",
        ask="Ask for an overview.",
        rubric_categories=["experience"],
        branches=[Branch(signal="default", next="END")],
    )


async def test_completed_answer_does_not_trigger_scoring(async_client, monkeypatch):
    # Scoring is a single holistic pass at finalize; completing an answer
    # mid-interview must NOT call the appraiser.
    state = _seed_interview()
    node = _completed_node()
    _fake_handle_turn(
        monkeypatch,
        _turn_result(reply="Thanks!", answer_complete=True, completed_question=node, answer_text="Full answer."),
    )

    def _must_not_run(*args, **kwargs):
        raise AssertionError("appraiser must not be called from the gateway")

    monkeypatch.setattr(appraiser_agent, "score_interview", _must_not_run)

    response = await async_client.post(_url(state.interview_id), json=_openai_body(), headers=_auth(state))

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Thanks!"
    for _ in range(3):  # any stray created task would run here
        await asyncio.sleep(0)
