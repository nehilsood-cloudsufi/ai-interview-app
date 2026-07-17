import asyncio
import json

import httpx
import respx

from app.config import settings
from app.routers import llm_gateway
from app.services import appraiser_agent, host_agent, interview_state
from app.services.host_agent import TurnResult
from app.services.interview_config import Branch, QuestionNode, get_rubric
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


# --- answer-complete background hook -----------------------------------------


def _completed_node() -> QuestionNode:
    return QuestionNode(
        id="company_overview",
        topic="company_overview",
        ask="Ask for an overview.",
        rubric_categories=["experience"],
        branches=[Branch(signal="default", next="END")],
    )


async def test_answer_complete_fires_background_hook(async_client, monkeypatch):
    state = _seed_interview()
    node = _completed_node()
    _fake_handle_turn(
        monkeypatch,
        _turn_result(reply="Thanks!", answer_complete=True, completed_question=node, answer_text="Full answer."),
    )

    hook_calls = []

    async def fake_hook(hook_state, question, answer_text):
        hook_calls.append((hook_state, question, answer_text))

    monkeypatch.setattr(llm_gateway, "_on_answer_complete", fake_hook)

    response = await async_client.post(_url(state.interview_id), json=_openai_body(), headers=_auth(state))

    assert response.status_code == 200
    for _ in range(3):  # let the created task run
        await asyncio.sleep(0)
    assert hook_calls == [(state, node, "Full answer.")]


async def test_answer_complete_hook_failure_is_logged_not_raised(async_client, monkeypatch, caplog):
    state = _seed_interview()
    _fake_handle_turn(
        monkeypatch,
        _turn_result(reply="Thanks!", answer_complete=True, completed_question=_completed_node(), answer_text="A."),
    )

    async def broken_hook(*args):
        raise RuntimeError("appraiser exploded")

    monkeypatch.setattr(llm_gateway, "_on_answer_complete", broken_hook)

    with caplog.at_level("WARNING", logger="app.routers.llm_gateway"):
        response = await async_client.post(_url(state.interview_id), json=_openai_body(), headers=_auth(state))
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "Thanks!"
        for _ in range(3):
            await asyncio.sleep(0)

    assert any("answer-complete hook failed" in record.message for record in caplog.records)


async def test_incomplete_answer_does_not_fire_hook(async_client, monkeypatch):
    state = _seed_interview()
    _fake_handle_turn(monkeypatch, _turn_result(answer_complete=False))

    hook_calls = []

    async def fake_hook(*args):
        hook_calls.append(args)

    monkeypatch.setattr(llm_gateway, "_on_answer_complete", fake_hook)

    response = await async_client.post(_url(state.interview_id), json=_openai_body(), headers=_auth(state))

    assert response.status_code == 200
    for _ in range(3):
        await asyncio.sleep(0)
    assert hook_calls == []


async def test_hook_awaits_appraiser_score_and_store(async_client, monkeypatch):
    # The real hook body: one score_and_store call per completed answer,
    # with the live state, the completed question, and the rubric singleton.
    state = _seed_interview()
    node = _completed_node()
    _fake_handle_turn(
        monkeypatch,
        _turn_result(reply="Thanks!", answer_complete=True, completed_question=node, answer_text="Full answer."),
    )

    calls = []

    async def fake_score_and_store(hook_state, question, answer_text, rubric):
        calls.append((hook_state, question, answer_text, rubric))

    monkeypatch.setattr(appraiser_agent, "score_and_store", fake_score_and_store)

    response = await async_client.post(_url(state.interview_id), json=_openai_body(), headers=_auth(state))

    assert response.status_code == 200
    for _ in range(3):  # let the created task run
        await asyncio.sleep(0)
    assert calls == [(state, node, "Full answer.", get_rubric())]


async def test_score_and_store_failure_does_not_affect_reply(async_client, monkeypatch, caplog):
    # score_and_store normally swallows its own failures; even if it raises,
    # the hook's outer try/except keeps the gateway reply intact.
    state = _seed_interview()
    _fake_handle_turn(
        monkeypatch,
        _turn_result(reply="Thanks!", answer_complete=True, completed_question=_completed_node(), answer_text="A."),
    )

    async def boom(*args, **kwargs):
        raise RuntimeError("appraiser exploded")

    monkeypatch.setattr(appraiser_agent, "score_and_store", boom)

    with caplog.at_level("WARNING", logger="app.routers.llm_gateway"):
        response = await async_client.post(_url(state.interview_id), json=_openai_body(), headers=_auth(state))
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "Thanks!"
        for _ in range(3):
            await asyncio.sleep(0)

    assert any("answer-complete hook failed" in record.message for record in caplog.records)


# --- end-to-end through the real host agent (Gemini mocked) -------------------


@respx.mock
def test_end_to_end_with_mocked_gemini(client, patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL, gemini_model="gemini-3.5-flash")
    content = json.dumps({"reply": "Thanks, Jane. Tell me about Acme.", "answer_complete": True, "branch_signal": "default"})
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    )
    state = _seed_interview()

    response = client.post(
        _url(state.interview_id),
        json=_openai_body(user_text="Hi, I'm Jane Doe, CTO of Acme Corp.", stream=True),
        headers=_auth(state),
    )

    assert response.status_code == 200
    frames = _parse_sse(response.text)
    assert frames[-1] == "[DONE]"
    streamed = "".join(json.loads(f)["choices"][0]["delta"].get("content", "") for f in frames[:-1])
    assert streamed == "Thanks, Jane. Tell me about Acme."

    # The real host agent ran: turns recorded and the state advanced past the
    # shipped questionnaire's start node.
    assert [turn.role for turn in state.turns] == ["candidate", "interviewer"]
    assert state.turns[0].text == "Hi, I'm Jane Doe, CTO of Acme Corp."
    assert state.current_node_id != "verify_identity"
