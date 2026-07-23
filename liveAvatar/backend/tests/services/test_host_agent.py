import asyncio
import dataclasses
import json

import httpx
import pytest
import respx

from app.config import settings
from app.models import TranscriptTurn
from app.services import host_agent
from app.services.host_agent import TurnResult, handle_turn
from app.services.interview_config import QuestionNode, RubricCategory, ValueOption
from app.services.interview_state import InterviewState, VendorProfile

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
CHAT_URL = f"{GEMINI_BASE_URL}chat/completions"


def make_questionnaire() -> dict[str, QuestionNode]:
    nodes = [
        QuestionNode(
            id="verify_identity",
            topic="identity_verification",
            ask="Confirm the vendor's company, contact name, and role.",
            rubric_categories=[],
            next="company_overview",
            max_followups=1,
        ),
        QuestionNode(
            id="company_overview",
            topic="company_overview",
            ask="Ask for a brief overview of the company.",
            rubric_categories=["experience"],
            next="ai_ml_depth",
            max_followups=1,
        ),
        QuestionNode(
            id="ai_ml_depth",
            topic="ai_ml_capability",
            ask="Ask about their AI/ML capabilities.",
            rubric_categories=["capability"],
            next="closing",
            max_followups=1,
        ),
        QuestionNode(
            id="closing",
            topic="closing",
            ask="Thank the vendor and wrap up.",
            rubric_categories=[],
            next="END",
            max_followups=0,
        ),
    ]
    return {node.id: node for node in nodes}


def make_rubric() -> dict[str, RubricCategory]:
    # Host never scores against these value_options - they exist only to
    # satisfy RubricCategory's constructor, since handle_turn takes the
    # rubric purely to know which categories a node's answer maps to.
    value_options = [ValueOption(label="High", points=100), ValueOption(label="Low", points=0)]
    return {
        "experience": RubricCategory(
            id="experience", name="Experience", weight=0.5, description="Track record.", value_options=value_options
        ),
        "capability": RubricCategory(
            id="capability",
            name="Capability",
            weight=0.5,
            description="Technical depth.",
            value_options=value_options,
        ),
    }


def make_state(
    node_id: str = "company_overview",
    followup_count: int = 0,
    turns: list[TranscriptTurn] | None = None,
) -> InterviewState:
    return InterviewState(
        interview_id="itest",
        gateway_token="tok",
        vendor_profile=VendorProfile(
            company_name="Acme Corp",
            contact_name="Jane Doe",
            contact_role="CTO",
        ),
        current_node_id=node_id,
        followup_count=followup_count,
        turns=turns if turns is not None else [],
    )


def gemini_response(
    reply: str = "Thanks!", answer_complete: bool = True, profile_updates: dict | None = None
) -> httpx.Response:
    body = {"reply": reply, "answer_complete": answer_complete}
    if profile_updates is not None:
        body["profile_updates"] = profile_updates
    content = json.dumps(body)
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


NULL_PROFILE_UPDATES = {
    "company_name": None,
    "contact_name": None,
    "contact_role": None,
}


@respx.mock
async def test_advances_on_complete_answer(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL, gemini_model="gemini-3.5-flash")
    route = respx.post(CHAT_URL).mock(
        return_value=gemini_response(reply="Great, tell me more about your AI work.")
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
    assert set(schema_spec["schema"]["required"]) == {"reply", "answer_complete", "profile_updates"}
    assert body["reasoning_effort"] == "low"
    assert body["max_tokens"] == 800
    assert body["messages"][0]["role"] == "system"
    system_content = body["messages"][0]["content"]
    assert "Acme Corp" in system_content
    assert "Jane Doe" in system_content
    assert "company_overview" in system_content
    assert body["messages"][-1]["role"] == "user"
    assert "We build ML pipelines for banks." in body["messages"][-1]["content"]


@respx.mock
async def test_system_content_includes_next_question(patch_settings):
    # The reply that closes a question must also ASK the next one (HeyGen only
    # calls the gateway when the vendor speaks - a bare acknowledgment stalls
    # the conversation), so the prompt carries the single next question.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state()  # at company_overview

    await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert "next question to ask in the same reply" in system_content
    assert "- Ask about their AI/ML capabilities." in system_content


@respx.mock
async def test_system_content_marks_end_as_closing(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response(reply="Thanks for your time!"))
    state = make_state(node_id="closing")

    await handle_turn(state, "That's everything from me.", make_questionnaire(), make_rubric())

    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert "- no further questions - thank them and close the interview." in system_content


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
async def test_incomplete_answer_stays_on_node_even_past_max_followups(patch_settings):
    # The follow-up-budget force-advance is GONE: repeated non-answers (asides,
    # repairs, fragments) must never consume script questions the vendor
    # hasn't heard answered (seen live 2026-07-22). Pacing is time's job -
    # time pressure upgrades incomplete answers, the wrap-up ends the script.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response(reply="No rush - what does the company focus on?", answer_complete=False)
    )
    prior_turns = [
        TranscriptTurn(role="candidate", text="We do stuff."),
        TranscriptTurn(role="interviewer", text="Could you expand on that?"),
    ]
    state = make_state(followup_count=1, turns=list(prior_turns))  # already at max_followups
    questionnaire = make_questionnaire()

    result = await handle_turn(state, "Just, you know, stuff.", questionnaire, make_rubric())

    assert result.reply == "No rush - what does the company focus on?"
    assert result.answer_complete is False
    assert result.completed_question is None
    assert state.current_node_id == "company_overview"  # did NOT advance
    assert state.followup_count == 2  # counter still tracks the exchanges
    assert len(state.turns) == 4


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
    content = '```json\n{"reply": "Got it, thanks!", "answer_complete": true}\n```'
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    )
    state = make_state()

    result = await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    assert route.call_count == 1
    assert result.reply == "Got it, thanks!"
    assert result.answer_complete is True
    assert state.current_node_id == "ai_ml_depth"


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
async def test_scout_findings_not_rendered_into_system_prompt(patch_settings):
    # Unbiased-interview rule: the Scout runs strictly AFTER the interview, so
    # its findings must never reach the Host's prompt even if present on state.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    from app.services.interview_state import ScoutFinding

    state = make_state()
    state.scout_findings = [
        ScoutFinding(topic="funding", summary="Raised a Series B in 2025.", source_url="https://news.example/acme"),
    ]

    await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert "Known vendor intel" not in system_content
    assert "funding" not in system_content


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
        return_value=gemini_response(reply="Thanks so much - we'll be in touch!")
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


# --- profile_updates merge ---------------------------------------------------


@respx.mock
async def test_profile_updates_merged_into_vendor_profile(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response(
            profile_updates={
                "company_name": "New Co",
                "contact_name": "Sam Lee",
                "contact_role": "VP Sales",
            }
        )
    )
    state = make_state()

    await handle_turn(
        state, "I'm Sam Lee, VP Sales at New Co - newco.example.", make_questionnaire(), make_rubric()
    )

    assert state.vendor_profile.company_name == "New Co"
    assert state.vendor_profile.contact_name == "Sam Lee"
    assert state.vendor_profile.contact_role == "VP Sales"


@respx.mock
async def test_all_null_profile_updates_leaves_profile_unchanged(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=gemini_response(profile_updates=NULL_PROFILE_UPDATES))
    state = make_state()
    original = dataclasses.replace(state.vendor_profile)

    await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    assert state.vendor_profile == original


@respx.mock
async def test_empty_string_profile_updates_are_ignored(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response(
            profile_updates={"company_name": "   ", "contact_name": "", "contact_role": None}
        )
    )
    state = make_state()
    original = dataclasses.replace(state.vendor_profile)

    await handle_turn(state, "Hi.", make_questionnaire(), make_rubric())

    assert state.vendor_profile == original


@respx.mock
async def test_late_correction_on_non_onboarding_node_overwrites(patch_settings):
    # The merge runs on EVERY turn, not just onboarding nodes, so a
    # correction mentioned mid-interview still sticks.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=gemini_response(
            reply="Got it, thanks for the correction.",
            profile_updates={
                "company_name": "Acme Corp International",
                "contact_name": None,
                "contact_role": None,
            },
        )
    )
    state = make_state(node_id="ai_ml_depth")  # not intro/confirm_profile

    await handle_turn(
        state, "Actually, it's Acme Corp International now.", make_questionnaire(), make_rubric()
    )

    assert state.vendor_profile.company_name == "Acme Corp International"
    # Untouched fields keep their prior values.
    assert state.vendor_profile.contact_name == "Jane Doe"


@respx.mock
async def test_soft_fail_leaves_profile_untouched(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500))
    state = make_state()
    original = dataclasses.replace(state.vendor_profile)

    result = await handle_turn(state, "Hello?", make_questionnaire(), make_rubric())

    assert result.reply == settings.host_fallback_reply
    assert state.vendor_profile == original


def test_merge_profile_updates_skips_locked_fields():
    # Direct unit test of the merge helper: a manually-locked field (e.g. via
    # PATCH /api/interview/{id}/profile) must never be overwritten by the
    # LLM's profile_updates, while unlocked fields still merge normally.
    profile = VendorProfile(
        company_name="Acme Corp", contact_name="Jane Doe", contact_role="CTO"
    )
    updates = {
        "company_name": "New Co",
        "contact_name": "Sam Lee",
        "contact_role": None,
    }

    host_agent._merge_profile_updates(profile, updates, locked={"company_name"})

    # Locked field untouched.
    assert profile.company_name == "Acme Corp"
    # Unlocked field still merges.
    assert profile.contact_name == "Sam Lee"


def test_merge_profile_updates_no_locked_fields_behaves_as_before():
    profile = VendorProfile(
        company_name="Acme Corp", contact_name="Jane Doe", contact_role="CTO"
    )
    updates = {
        "company_name": "New Co",
        "contact_name": None,
        "contact_role": None,
    }

    host_agent._merge_profile_updates(profile, updates, locked=set())

    assert profile.company_name == "New Co"


# --- streaming turn ----------------------------------------------------------


def _sse(*deltas: str) -> str:
    frames = [f"data: {json.dumps({'choices': [{'delta': {'content': d}}]})}\n\n" for d in deltas]
    return "".join(frames) + "data: [DONE]\n\n"


def stream_response(
    reply="Thanks!", answer_complete=True, profile_updates: dict | None = None, *, chunk_size=None
) -> httpx.Response:
    body = {"reply": reply, "answer_complete": answer_complete}
    if profile_updates is not None:
        body["profile_updates"] = profile_updates
    content = json.dumps(body)
    if chunk_size is None:
        deltas = [content]
    else:
        deltas = [content[i : i + chunk_size] for i in range(0, len(content), chunk_size)]
    return httpx.Response(200, text=_sse(*deltas))


async def _collect_stream(state, user_text, questionnaire, rubric):
    outcome = host_agent.StreamedTurn()
    deltas = [d async for d in host_agent.stream_turn(state, user_text, questionnaire, rubric, outcome)]
    return deltas, outcome


@respx.mock
async def test_stream_turn_streams_reply_and_advances(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL, gemini_model="gemini-3.5-flash")
    respx.post(CHAT_URL).mock(
        return_value=stream_response(reply="Great, tell me more about your AI work.", chunk_size=5)
    )
    state = make_state()
    questionnaire = make_questionnaire()

    deltas, outcome = await _collect_stream(state, "We build ML pipelines.", questionnaire, make_rubric())

    # Reply arrived in multiple chunks and reassembles to the full spoken line.
    assert len(deltas) > 1
    assert "".join(deltas) == "Great, tell me more about your AI work."
    # State advanced exactly as the non-streaming path would.
    assert outcome.result.answer_complete is True
    assert outcome.result.completed_question is questionnaire["company_overview"]
    assert state.current_node_id == "ai_ml_depth"
    assert state.followup_count == 0
    assert [turn.role for turn in state.turns] == ["candidate", "interviewer"]
    assert state.turns[1].text == "Great, tell me more about your AI work."


@respx.mock
async def test_stream_turn_followup_on_incomplete(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=stream_response(reply="Could you expand on that?", answer_complete=False)
    )
    state = make_state()

    deltas, outcome = await _collect_stream(state, "We do stuff.", make_questionnaire(), make_rubric())

    assert "".join(deltas) == "Could you expand on that?"
    assert outcome.result.answer_complete is False
    assert state.current_node_id == "company_overview"
    assert state.followup_count == 1


@respx.mock
async def test_stream_turn_sends_stream_flag_and_schema(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=stream_response())
    state = make_state()

    await _collect_stream(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    body = json.loads(route.calls[0].request.content)
    assert body["stream"] is True
    # reply must be the first schema property so it streams first.
    assert list(body["response_format"]["json_schema"]["schema"]["properties"])[0] == "reply"
    assert body["reasoning_effort"] == "low"


async def test_stream_turn_end_node_yields_closing_without_call(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    state = make_state(node_id="END")
    with respx.mock:
        deltas, outcome = await _collect_stream(state, "Anything else?", make_questionnaire(), make_rubric())
        assert len(respx.calls) == 0

    assert "".join(deltas) == settings.host_closing_reply
    assert state.turns == []
    assert state.current_node_id == "END"


async def test_stream_turn_missing_key_raises(patch_settings):
    patch_settings(gemini_api_key=None)
    state = make_state()
    with respx.mock:
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            await _collect_stream(state, "Hi.", make_questionnaire(), make_rubric())
        assert len(respx.calls) == 0


@respx.mock
async def test_stream_turn_http_error_before_emit_yields_fallback(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500))
    state = make_state()

    deltas, outcome = await _collect_stream(state, "Hello?", make_questionnaire(), make_rubric())

    # Nothing spoken yet -> soft-fall to the canned reply, state untouched.
    assert "".join(deltas) == settings.host_fallback_reply
    assert outcome.result.reply == settings.host_fallback_reply
    assert state.turns == []
    assert state.current_node_id == "company_overview"


@respx.mock
async def test_stream_turn_merges_profile_updates_identically(patch_settings):
    # Buffered and streaming paths must drive state (including the profile
    # merge) identically - both funnel through _apply_outcome.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(
        return_value=stream_response(
            reply="Great, thanks!",
            profile_updates={
                "company_name": "New Co",
                "contact_name": "Sam Lee",
                "contact_role": None,
            },
        )
    )
    state = make_state()

    await _collect_stream(state, "I'm Sam Lee at New Co.", make_questionnaire(), make_rubric())

    assert state.vendor_profile.company_name == "New Co"
    assert state.vendor_profile.contact_name == "Sam Lee"
    # Fields not reported this turn keep their prior values.
    assert state.vendor_profile.contact_role == "CTO"


@respx.mock
async def test_stream_turn_truncated_trailing_json_keeps_spoken_reply(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    # Reply string completes, but the object is cut off before the routing
    # fields parse. The reply is already spoken; state is left unchanged.
    truncated = '{"reply": "All good, moving on.", "answer_complete": tru'
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, text=_sse(truncated)))
    state = make_state()

    deltas, outcome = await _collect_stream(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    assert "".join(deltas) == "All good, moving on."
    assert state.current_node_id == "company_overview"  # not advanced
    assert state.turns == []


# host_agent must be listed in conftest._SETTINGS_IMPORTERS or patch_settings
# silently won't reach it; this guards against that regression.
def test_host_agent_settings_are_patchable(patch_settings):
    patched = patch_settings(gemini_api_key="sentinel-key")
    assert host_agent.settings is patched


# --- mode-aware system prompt (H3: terse typed answers in chat mode) --------


@respx.mock
async def test_handle_turn_avatar_mode_omits_chat_prompt(patch_settings):
    # Default mode ("avatar") must not carry any chat-mode text - each mode
    # appends only its own suffix.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state()

    await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert settings.host_chat_mode_prompt not in system_content


@respx.mock
async def test_handle_turn_avatar_mode_appends_avatar_prompt(patch_settings):
    # Avatar mode carries the VAD-fragment guidance so an unfinished spoken
    # fragment ("...We provide" / "and") isn't judged a complete answer.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state()

    await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert settings.host_avatar_mode_prompt in system_content
    # Off-topic deflection (T3-B1/T4-B1) must reach the LLM in every mode.
    assert "Never answer the vendor's questions" in system_content


@respx.mock
async def test_handle_turn_chat_mode_omits_avatar_prompt(patch_settings):
    # Typed chat input is never VAD-fragmented - the avatar-mode guidance
    # must not leak into chat mode (it would fight the terse-answers rule).
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state()

    await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric(), mode="chat")

    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert settings.host_avatar_mode_prompt not in system_content


@respx.mock
async def test_cancelled_turn_is_skipped_before_the_gemini_call(patch_settings):
    # HeyGen cancels a request the moment a newer speech fragment supersedes
    # it. A turn whose request is already gone must be skipped entirely:
    # no Gemini call, no state mutation, None returned.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state()

    async def already_cancelled():
        return True

    result = await handle_turn(
        state, "I", make_questionnaire(), make_rubric(), is_cancelled=already_cancelled
    )

    assert result is None
    assert not route.called
    assert state.current_node_id == "company_overview"
    assert state.turns == []
    assert state.followup_count == 0


@respx.mock
async def test_turn_cancelled_during_gemini_call_discards_outcome(patch_settings):
    # Cancellation landing while Gemini is answering: the reply will never be
    # spoken, so the outcome must be discarded before any state mutation -
    # "if the vendor never heard it, it didn't happen".
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state()

    checks = iter([False, True])  # pre-check passes, post-Gemini check reports cancelled

    async def cancelled_mid_call():
        return next(checks)

    result = await handle_turn(
        state, "work as an engineer at", make_questionnaire(), make_rubric(), is_cancelled=cancelled_mid_call
    )

    assert result is None
    assert route.call_count == 1
    assert state.current_node_id == "company_overview"
    assert state.turns == []
    assert state.followup_count == 0


@respx.mock
async def test_cancelled_stream_turn_is_skipped_before_the_gemini_call(patch_settings):
    # Streaming counterpart of the buffered skip: a superseded streaming turn
    # yields nothing, makes no Gemini call, and leaves state untouched.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state()
    outcome = host_agent.StreamedTurn()

    async def already_cancelled():
        return True

    chunks = [
        text
        async for text in host_agent.stream_turn(
            state, "I", make_questionnaire(), make_rubric(), outcome, is_cancelled=already_cancelled
        )
    ]

    assert chunks == []
    assert outcome.result is None
    assert not route.called
    assert state.current_node_id == "company_overview"
    assert state.turns == []


@respx.mock
async def test_concurrent_turns_serialize_per_interview(patch_settings):
    # HeyGen's VAD can fire overlapping gateway calls when it splits one
    # flowing answer into fragments (seen live 2026-07-22: two turns processed
    # concurrently on the same node). state.turn_lock must serialize them so
    # the second turn sees the node the first advanced to - never the same
    # node twice, never a skipped question.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)

    async def slow_response(request):
        await asyncio.sleep(0.05)  # keep the first turn in-flight while the second arrives
        return gemini_response()

    respx.post(CHAT_URL).mock(side_effect=slow_response)
    state = make_state()  # starts at company_overview
    questionnaire = make_questionnaire()

    first, second = await asyncio.gather(
        handle_turn(state, "We build ML pipelines. We provide", questionnaire, make_rubric()),
        handle_turn(state, "solutions to every problem our clients have.", questionnaire, make_rubric()),
    )

    completed = {result.completed_question.id for result in (first, second)}
    assert completed == {"company_overview", "ai_ml_depth"}
    assert state.current_node_id == "closing"


@respx.mock
async def test_handle_turn_chat_mode_appends_chat_prompt(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state()

    await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric(), mode="chat")

    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert settings.host_chat_mode_prompt in system_content
    # Off-topic deflection (T3-B1/T4-B1) must reach the LLM in every mode.
    assert "Never answer the vendor's questions" in system_content


@respx.mock
async def test_stream_turn_avatar_mode_omits_chat_prompt(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=stream_response())
    state = make_state()

    await _collect_stream(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert settings.host_chat_mode_prompt not in system_content


@respx.mock
async def test_stream_turn_chat_mode_appends_chat_prompt(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=stream_response())
    state = make_state()

    outcome = host_agent.StreamedTurn()
    deltas = [
        d
        async for d in host_agent.stream_turn(
            state, "We build ML pipelines.", make_questionnaire(), make_rubric(), outcome, mode="chat"
        )
    ]
    assert deltas  # sanity: the turn still streamed a reply

    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert settings.host_chat_mode_prompt in system_content


# --- Time-aware wrap-up (clocked, i.e. prod-tier avatar sessions) ---


def _clocked_state(seconds_elapsed: float, max_session_seconds: int = 300) -> InterviewState:
    from datetime import datetime, timedelta, timezone

    state = make_state()
    state.max_session_seconds = max_session_seconds
    state.first_turn_at = datetime.now(timezone.utc) - timedelta(seconds=seconds_elapsed)
    return state


@respx.mock
async def test_time_generous_prompt_when_ample_time_remains(patch_settings):
    # Fresh clocked interview (600s booked, just started): remaining is well
    # above host_time_generous_seconds (180), so the Host is told to dig
    # deeper on brief answers instead of racing through the script.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = _clocked_state(seconds_elapsed=10, max_session_seconds=600)

    await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert settings.host_time_generous_prompt in system_content
    assert settings.host_time_pressure_prompt not in system_content


@respx.mock
async def test_no_time_generous_prompt_without_a_clock(patch_settings):
    # Unclocked interview (dev tier / chat): there is no session clock, so
    # neither pacing prompt applies.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state()

    await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert settings.host_time_generous_prompt not in system_content


@respx.mock
async def test_no_time_generous_prompt_when_clock_runs_low(patch_settings):
    # 300s booked, 150s elapsed -> 150s remaining: below the generous
    # threshold but above time pressure - neither pacing prompt applies.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = _clocked_state(seconds_elapsed=150, max_session_seconds=300)

    await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert settings.host_time_generous_prompt not in system_content
    assert settings.host_time_pressure_prompt not in system_content


@respx.mock
async def test_wrapup_when_time_nearly_exhausted(patch_settings):
    # Remaining 50s <= host_wrapup_seconds (60): canned closing, script
    # skipped to END, NO Gemini call.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = _clocked_state(seconds_elapsed=250)

    result = await handle_turn(state, "And another thing...", make_questionnaire(), make_rubric())

    assert result.reply == settings.host_timeup_reply
    assert result.answer_complete is False
    assert state.current_node_id == "END"
    assert [turn.role for turn in state.turns] == ["candidate", "interviewer"]
    assert state.turns[1].text == settings.host_timeup_reply
    assert not route.called


@respx.mock
async def test_time_pressure_forces_advance_and_prompt(patch_settings):
    # Remaining 100s: between wrapup (60) and pressure (120) thresholds ->
    # normal Gemini turn, but the system prompt carries the time-pressure
    # suffix and an "incomplete" judgement still advances the script.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(
        return_value=gemini_response(reply="Quickly then - your AI work?", answer_complete=False)
    )
    state = _clocked_state(seconds_elapsed=200)

    result = await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    assert result.answer_complete is True
    assert state.current_node_id == "ai_ml_depth"
    body = json.loads(route.calls[0].request.content)
    assert settings.host_time_pressure_prompt in body["messages"][0]["content"]


@respx.mock
async def test_no_time_pressure_with_plenty_of_time(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = _clocked_state(seconds_elapsed=10)

    await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    body = json.loads(route.calls[0].request.content)
    assert settings.host_time_pressure_prompt not in body["messages"][0]["content"]


@respx.mock
async def test_first_clocked_turn_stamps_first_turn_at(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state()
    state.max_session_seconds = 300
    assert state.first_turn_at is None

    await handle_turn(state, "Hello!", make_questionnaire(), make_rubric())

    assert state.first_turn_at is not None


@respx.mock
async def test_unclocked_interview_never_stamps_or_paces(patch_settings):
    # Dev tier: max_session_seconds is None -> no clock, byte-identical flow.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state()

    await handle_turn(state, "Hello!", make_questionnaire(), make_rubric())

    assert state.first_turn_at is None


@respx.mock
async def test_chat_mode_ignores_session_clock(patch_settings):
    # Chat mode has no HeyGen session/cap even on the prod tier - an ancient
    # first_turn_at must not trigger the wrap-up.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = _clocked_state(seconds_elapsed=9999)

    result = await handle_turn(state, "Hi!", make_questionnaire(), make_rubric(), mode="chat")

    assert route.called
    assert result.reply != settings.host_timeup_reply


# --- Question-plan advancement + intake context (pre-interview intake) ---


@respx.mock
async def test_advances_along_question_plan_skipping_nodes(patch_settings):
    # A short prod session's plan may omit questionnaire nodes; completing a
    # question must advance to the PLAN's next node (and the prompt must
    # announce that node's ask), not the questionnaire's own `next` pointer.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL, gemini_model="gemini-3.5-flash")
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state()
    state.question_plan = ["company_overview", "closing"]  # skips ai_ml_depth
    questionnaire = make_questionnaire()

    result = await handle_turn(state, "We build ML pipelines for banks.", questionnaire, make_rubric())

    assert result.answer_complete is True
    assert state.current_node_id == "closing"
    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert "Thank the vendor and wrap up." in system_content
    assert "Ask about their AI/ML capabilities." not in system_content


@respx.mock
async def test_plan_end_advances_to_end_node(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL, gemini_model="gemini-3.5-flash")
    respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state(node_id="ai_ml_depth")
    state.question_plan = ["company_overview", "ai_ml_depth"]

    await handle_turn(state, "We fine-tune our own models.", make_questionnaire(), make_rubric())

    assert state.current_node_id == host_agent.END_NODE_ID


@respx.mock
async def test_vendor_context_and_greeting_in_first_turn_prompt(patch_settings):
    # First exchange of an interview with intake material: the system prompt
    # carries the vendor-provided background bullets (phrasing-only, with the
    # never-skip rule) and the one-sentence greeting instruction.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL, gemini_model="gemini-3.5-flash")
    route = respx.post(CHAT_URL).mock(return_value=gemini_response(answer_complete=False))
    state = make_state()
    state.vendor_context = "- Builds document-intelligence pipelines for banks"

    await handle_turn(state, "Hi there!", make_questionnaire(), make_rubric())

    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert "Vendor-provided background" in system_content
    assert "- Builds document-intelligence pipelines for banks" in system_content
    assert "Never skip or shorten a question because of it" in system_content
    assert "opening exchange" in system_content


@respx.mock
async def test_no_context_or_greeting_sections_mid_interview(patch_settings):
    # Past the first exchange with no intake material, neither section renders.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL, gemini_model="gemini-3.5-flash")
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state(
        turns=[
            TranscriptTurn(role="candidate", text="Hello!"),
            TranscriptTurn(role="interviewer", text="Welcome - tell me about your company."),
        ]
    )

    await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    system_content = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert "Vendor-provided background" not in system_content
    assert "opening exchange" not in system_content


# --- Bug 2 (T3-B1/T4-B1): off-topic deflection ------------------------------


def test_system_prompt_forbids_answering_vendor_questions():
    # Buried-parenthetical deflection was too weak (a live test showed the
    # Host answering "what is RAG?" instead of deferring it) - the prompt
    # must now explicitly forbid answering and give a concrete example.
    assert "Never answer the vendor's questions" in settings.host_system_prompt
    assert "Can you explain what RAG is?" in settings.host_system_prompt


# --- Bug 1 (T3-B2, critical): chat-mode limits, retry, escape hatch ---------


@respx.mock
async def test_chat_mode_payload_uses_chat_token_cap(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(return_value=gemini_response())
    state = make_state()

    await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric(), mode="chat")

    body = json.loads(route.calls[0].request.content)
    assert body["max_tokens"] == 4000


async def test_chat_mode_uses_generous_timeout(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    captured = {}

    async def fake_chat_completion(payload, *, timeout, fallback_model=None):
        captured["timeout"] = timeout
        content = json.dumps({"reply": "Thanks!", "answer_complete": True})
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(host_agent.gemini_client, "chat_completion", fake_chat_completion)
    state = make_state()

    await handle_turn(state, "A very long detailed answer.", make_questionnaire(), make_rubric(), mode="chat")

    assert captured["timeout"] == 60.0


async def test_avatar_mode_timeout_unchanged(patch_settings, monkeypatch):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    captured = {}

    async def fake_chat_completion(payload, *, timeout, fallback_model=None):
        captured["timeout"] = timeout
        content = json.dumps({"reply": "Thanks!", "answer_complete": True})
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(host_agent.gemini_client, "chat_completion", fake_chat_completion)
    state = make_state()

    await handle_turn(state, "We build ML pipelines.", make_questionnaire(), make_rubric())

    assert captured["timeout"] == 8.0


@respx.mock
async def test_read_timeout_soft_fails_and_preserves_state(patch_settings):
    # Chat mode's generous timeout still has to fail eventually if Gemini
    # never answers - the soft-fail contract must hold identically to HTTP
    # errors (this was the critical wedge: a detailed typed answer hitting
    # the avatar-sized 8s/800-token limits, forever).
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    respx.post(CHAT_URL).mock(side_effect=httpx.ReadTimeout("timed out"))
    state = make_state()
    original_profile = dataclasses.replace(state.vendor_profile)

    result = await handle_turn(
        state, "A very long, detailed typed answer.", make_questionnaire(), make_rubric(), mode="chat"
    )

    assert result.reply == settings.host_fallback_reply
    assert state.turns == []
    assert state.current_node_id == "company_overview"
    assert state.vendor_profile == original_profile


@respx.mock
async def test_parse_retry_doubles_max_tokens(patch_settings):
    # Token truncation is the dominant parse-failure cause, so the retry
    # resends with a doubled cap instead of an identical "fresh sample".
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": "not json at all"}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "still not json"}}]}),
        ]
    )
    state = make_state()

    result = await handle_turn(state, "Hello?", make_questionnaire(), make_rubric())

    assert route.call_count == 2
    first_body = json.loads(route.calls[0].request.content)
    second_body = json.loads(route.calls[1].request.content)
    assert first_body["max_tokens"] == 800
    assert second_body["max_tokens"] == 1600
    # Both attempts exhausted -> handle_turn-level soft-fail, state untouched.
    assert result.reply == settings.host_fallback_reply
    assert state.turns == []


@respx.mock
async def test_truncated_json_soft_fails(patch_settings):
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    truncated = '{"reply": "We are almost done here, just a bit mo'
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": truncated}}]})
    )
    state = make_state()

    result = await handle_turn(state, "Tell me more.", make_questionnaire(), make_rubric(), mode="chat")

    assert result.reply == settings.host_fallback_reply
    assert state.turns == []
    assert state.current_node_id == "company_overview"


@respx.mock
async def test_consecutive_soft_fails_switch_fallback_reply(patch_settings):
    # A persistent failure must not loop the identical "say that again" line
    # forever - after 2+ consecutive failures the vendor hears a different
    # line hinting that a shorter message may help. A later success resets
    # the counter so a single subsequent failure gets the plain fallback again.
    patch_settings(gemini_api_key="gem-key", gemini_base_url=GEMINI_BASE_URL)
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(500),
            gemini_response(reply="Great, thanks!"),
            httpx.Response(500),
        ]
    )
    state = make_state()
    questionnaire = make_questionnaire()

    first = await handle_turn(state, "Hello?", questionnaire, make_rubric())
    assert first.reply == settings.host_fallback_reply
    assert state.consecutive_soft_fails == 1

    second = await handle_turn(state, "Hello?", questionnaire, make_rubric())
    assert second.reply == settings.host_fallback_reply_repeat
    assert state.consecutive_soft_fails == 2

    third = await handle_turn(state, "We build ML pipelines.", questionnaire, make_rubric())
    assert third.reply == "Great, thanks!"
    assert state.consecutive_soft_fails == 0
    assert third.answer_complete is True

    fourth = await handle_turn(state, "Hello?", questionnaire, make_rubric())
    assert fourth.reply == settings.host_fallback_reply
    assert state.consecutive_soft_fails == 1

    assert route.call_count == 4
