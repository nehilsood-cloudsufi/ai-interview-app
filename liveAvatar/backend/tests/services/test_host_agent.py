import json

import httpx
import pytest
import respx

from app.models import TranscriptTurn
from app.services import host_agent
from app.services.host_agent import TurnResult, handle_turn
from app.services.interview_config import Branch, QuestionNode, RubricCategory
from app.services.interview_state import InterviewState, ScoutFinding, VendorProfile

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
CHAT_URL = f"{GEMINI_BASE_URL}chat/completions"


def make_questionnaire() -> dict[str, QuestionNode]:
    nodes = [
        QuestionNode(
            id="verify_identity",
            topic="identity_verification",
            ask="Confirm the vendor's company, contact name, and role.",
            rubric_categories=[],
            branches=[Branch(signal="default", next="company_overview")],
            max_followups=1,
        ),
        QuestionNode(
            id="company_overview",
            topic="company_overview",
            ask="Ask for a brief overview of the company.",
            rubric_categories=["experience"],
            branches=[
                Branch(signal="mentions_ai_ml", next="ai_ml_depth"),
                Branch(signal="default", next="closing"),
            ],
            max_followups=1,
        ),
        QuestionNode(
            id="ai_ml_depth",
            topic="ai_ml_capability",
            ask="Go deeper on their AI/ML capabilities.",
            rubric_categories=["capability"],
            branches=[Branch(signal="default", next="closing")],
            max_followups=1,
        ),
        QuestionNode(
            id="closing",
            topic="closing",
            ask="Thank the vendor and wrap up.",
            rubric_categories=[],
            branches=[Branch(signal="finished", next="END")],
            max_followups=0,
        ),
    ]
    return {node.id: node for node in nodes}


def make_rubric() -> dict[str, RubricCategory]:
    return {
        "experience": RubricCategory(id="experience", name="Experience", weight=0.5, description="Track record."),
        "capability": RubricCategory(id="capability", name="Capability", weight=0.5, description="Technical depth."),
    }


def make_state(
    node_id: str = "company_overview",
    followup_count: int = 0,
    turns: list[TranscriptTurn] | None = None,
    scout_findings: list[ScoutFinding] | None = None,
) -> InterviewState:
    return InterviewState(
        interview_id="itest",
        gateway_token="tok",
        vendor_profile=VendorProfile(
            company_name="Acme Corp",
            website="https://acme.example",
            contact_name="Jane Doe",
            contact_role="CTO",
        ),
        current_node_id=node_id,
        followup_count=followup_count,
        turns=turns if turns is not None else [],
        scout_findings=scout_findings if scout_findings is not None else [],
    )


def gemini_response(reply: str = "Thanks!", answer_complete: bool = True, branch_signal: str = "default") -> httpx.Response:
    content = json.dumps(
        {"reply": reply, "answer_complete": answer_complete, "branch_signal": branch_signal}
    )
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@respx.mock
async def test_advances_on_complete_answer(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL, gemini_model="gemini-3.5-flash")
    route = respx.post(CHAT_URL).mock(
        return_value=gemini_response(reply="Great, tell me more about your AI work.", branch_signal="mentions_ai_ml")
    )
    state = make_state()
    questionnaire = make_questionnaire()

    result = await handle_turn(state, "We build ML pipelines for banks.", questionnaire, make_rubric())

    assert isinstance(result, TurnResult)
    assert result.reply == "Great, tell me more about your AI work."
    assert result.answer_complete is True
    assert result.completed_question is questionnaire["company_overview"]
    assert result.answer_text == "We build ML pipelines for banks."
    assert state.current_node_id == "ai_ml_depth"
    assert state.followup_count == 0
    assert [turn.role for turn in state.turns] == ["candidate", "interviewer"]
    assert state.turns[0].text == "We build ML pipelines for banks."
    assert state.turns[1].text == "Great, tell me more about your AI work."

    # Request shape mirrors summary_service's raw-httpx Gemini call.
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer gem-key"
    body = json.loads(request.content)
    assert body["model"] == "gemini-3.5-flash"
    # Strict structured output + low reasoning + bounded reply: the fixes for
    # the malformed-JSON and thinking-latency issues from the 2026-07-18 live test.
    assert body["response_format"]["type"] == "json_schema"
    schema_spec = body["response_format"]["json_schema"]
    assert schema_spec["name"] == "host_turn"
    assert schema_spec["strict"] is True
    assert set(schema_spec["schema"]["required"]) == {"reply", "answer_complete", "branch_signal"}
    assert body["reasoning_effort"] == "low"
    assert body["max_tokens"] == 500
    assert body["messages"][0]["role"] == "system"
    system_content = body["messages"][0]["content"]
    assert "Acme Corp" in system_content
    assert "Jane Doe" in system_content
    assert "company_overview" in system_content
    assert "mentions_ai_ml" in system_content
    assert body["messages"][-1]["role"] == "user"
    assert "We build ML pipelines for banks." in body["messages"][-1]["content"]


@respx.mock
async def test_followup_on_incomplete_answer(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response(reply="Could you expand on that?", answer_complete=False)
    )
    state = make_state()

    result = await handle_turn(state, "We do stuff.", make_questionnaire(), make_rubric())

    assert result.reply == "Could you expand on that?"
    assert result.answer_complete is False
    assert result.completed_question is None
    assert result.answer_text == ""
    assert state.current_node_id == "company_overview"
    assert state.followup_count == 1
    assert [turn.role for turn in state.turns] == ["candidate", "interviewer"]


@respx.mock
async def test_force_advance_when_followups_exceed_max(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response(reply="Alright, let's move on.", answer_complete=False)
    )
    prior_turns = [
        TranscriptTurn(role="candidate", text="We do stuff."),
        TranscriptTurn(role="interviewer", text="Could you expand on that?"),
    ]
    state = make_state(followup_count=1, turns=list(prior_turns))
    questionnaire = make_questionnaire()

    result = await handle_turn(state, "Just, you know, stuff.", questionnaire, make_rubric())

    # Advanced exactly as if complete, via the default branch.
    assert result.reply == "Alright, let's move on."
    assert result.answer_complete is True
    assert result.completed_question is questionnaire["company_overview"]
    assert "We do stuff." in result.answer_text
    assert "Just, you know, stuff." in result.answer_text
    assert state.current_node_id == "closing"
    assert state.followup_count == 0
    assert len(state.turns) == 4


@respx.mock
async def test_unknown_branch_signal_falls_to_default(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response(branch_signal="totally_made_up")
    )
    state = make_state()

    await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    assert state.current_node_id == "closing"


@respx.mock
async def test_malformed_gemini_json_retried_then_falls_back(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "not json at all"}}]})
    )
    state = make_state(followup_count=1)

    result = await handle_turn(state, "Hello?", make_questionnaire(), make_rubric())

    # One retry on a parse failure, then the soft-fail path.
    assert route.call_count == 2
    assert result.reply == "I'm sorry, could you say that again?"
    assert result.answer_complete is False
    assert result.completed_question is None
    assert result.answer_text == ""
    assert state.turns == []
    assert state.current_node_id == "company_overview"
    assert state.followup_count == 1


@respx.mock
async def test_malformed_gemini_json_recovers_on_retry(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": '{"broken":'}}]}),
            gemini_response(reply="Sorry - could you tell me more?", answer_complete=False),
        ]
    )
    state = make_state()

    result = await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    assert route.call_count == 2
    assert result.reply == "Sorry - could you tell me more?"
    assert state.followup_count == 1
    assert [turn.role for turn in state.turns] == ["candidate", "interviewer"]


@respx.mock
async def test_fenced_json_content_is_parsed(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    content = '```json\n{"reply": "Got it, thanks!", "answer_complete": true, "branch_signal": "default"}\n```'
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    )
    state = make_state()

    result = await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    assert route.call_count == 1
    assert result.reply == "Got it, thanks!"
    assert result.answer_complete is True
    assert state.current_node_id == "closing"


@respx.mock
async def test_http_error_is_not_retried(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(500))
    state = make_state()

    result = await handle_turn(state, "Hello?", make_questionnaire(), make_rubric())

    # HTTP failures fall back immediately - no retry inside HeyGen's timeout window.
    assert route.call_count == 1
    assert result.reply == "I'm sorry, could you say that again?"


@respx.mock
async def test_http_error_leaves_state_unchanged(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500))
    state = make_state()

    result = await handle_turn(state, "Hello?", make_questionnaire(), make_rubric())

    assert result.reply == "I'm sorry, could you say that again?"
    assert state.turns == []
    assert state.current_node_id == "company_overview"


async def test_missing_gemini_key_raises(patch_settings):
    patch_settings(gemini_api_key=None)
    state = make_state()
    with respx.mock:
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            await handle_turn(state, "Hi.", make_questionnaire(), make_rubric())
        assert len(respx.calls) == 0


@respx.mock
async def test_scout_findings_rendered_into_system_prompt(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    findings = [
        ScoutFinding(topic="funding", summary="Raised a Series B in 2025.", source_url="https://news.example/acme"),
        ScoutFinding(topic="clients", summary="Lists two Fortune 500 clients.", source_url=None),
    ]
    state = make_state(scout_findings=findings)

    await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert "Known vendor intel" in system_content
    assert "- funding: Raised a Series B in 2025." in system_content
    assert "- clients: Lists two Fortune 500 clients." in system_content


@respx.mock
async def test_no_scout_findings_omits_intel_block(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state(scout_findings=[])

    await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert "Known vendor intel" not in system_content


async def test_end_node_returns_closing_reply_without_gemini_call(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    state = make_state(node_id="END")
    with respx.mock:
        result = await handle_turn(state, "Anything else?", make_questionnaire(), make_rubric())
        assert len(respx.calls) == 0

    assert result.reply
    assert result.answer_complete is False
    assert result.completed_question is None
    assert result.answer_text == ""
    assert state.turns == []
    assert state.current_node_id == "END"


@respx.mock
async def test_advancing_into_end_uses_llm_reply(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response(reply="Thanks so much - we'll be in touch!", branch_signal="finished")
    )
    state = make_state(node_id="closing")
    questionnaire = make_questionnaire()

    result = await handle_turn(state, "Thanks, goodbye.", questionnaire, make_rubric())

    assert result.reply == "Thanks so much - we'll be in touch!"
    assert result.completed_question is questionnaire["closing"]
    assert state.current_node_id == "END"


@respx.mock
async def test_transcript_window_limits_history(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    old_turns = [
        TranscriptTurn(role="candidate" if i % 2 == 0 else "interviewer", text=f"turn-{i}")
        for i in range(14)
    ]
    state = make_state(turns=list(old_turns))

    await handle_turn(state, "Latest answer.", make_questionnaire(), make_rubric())

    user_content = json.loads(route.calls[0].request.content)["messages"][-1]["content"]
    assert "turn-13" in user_content
    assert "turn-4" in user_content
    assert "turn-3" not in user_content
    assert "Latest answer." in user_content


# host_agent must be listed in conftest._SETTINGS_IMPORTERS or patch_settings
# silently won't reach it; this guards against that regression.
def test_host_agent_settings_are_patchable(patch_settings):
    patched = patch_settings(gemini_api_key="sentinel-key")
    assert host_agent.settings is patched
