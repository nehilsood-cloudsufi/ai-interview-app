import logging

from app.models import TranscriptTurn
from app.services import coordinator_agent, evaluator_agent, interview_state, pipeline, scout_agent, transcript_store
from app.services.evaluator_agent import CategoryScore, Scorecard
from app.services.interview_config import get_rubric
from app.services.interview_state import ScoutFinding, VendorProfile


def make_state() -> interview_state.InterviewState:
    state = interview_state.create(VendorProfile(company_name="Acme Corp"), "ai_ml")
    state.turns = [
        TranscriptTurn(role="interviewer", text="Tell me about your company."),
        TranscriptTurn(role="candidate", text="We build ML pipelines for banks."),
    ]
    return state


def make_scorecard(scores: dict[str, float | None], overall: float | None) -> Scorecard:
    return Scorecard(
        categories=[
            CategoryScore(id=c.id, name=c.name, weight=c.weight, score=scores.get(c.id), evidence=[])
            for c in get_rubric().values()
        ],
        overall=overall,
    )


def make_payload(session_id: str = "sid") -> dict:
    return {
        "session_id": session_id,
        "created_at": "2026-01-01T00:00:00+00:00",
        "turns": [],
        "summary": "a summary",
        "summary_ok": True,
        "pipeline_status": "interviewed",
    }


async def test_run_happy_path_transitions_scouting_evaluating_ready(monkeypatch):
    transitions: list[tuple[str, str | None]] = []
    findings = [ScoutFinding(topic="news", summary="No recent press.", source_url=None)]
    scorecard = make_scorecard({"experience": 4.0}, overall=4.0)

    async def fake_scout_run(state):
        transitions.append(("scout", state.pipeline_status))
        return findings

    async def fake_score_interview(turns, rubric, scout_findings):
        transitions.append(("evaluator", state.pipeline_status))
        return scorecard

    save_calls: list[tuple[str, dict]] = []

    async def fake_save(session_id, payload):
        save_calls.append((session_id, dict(payload)))

    monkeypatch.setattr(scout_agent, "run", fake_scout_run)
    monkeypatch.setattr(evaluator_agent, "score_interview", fake_score_interview)
    monkeypatch.setattr(transcript_store, "save", fake_save)

    state = make_state()
    payload = make_payload()

    await pipeline.run(state, "sid", payload)

    assert transitions == [("scout", "scouting"), ("evaluator", "evaluating")]
    assert state.pipeline_status == "ready"
    assert state.scorecard == scorecard
    # experience 4.0 -> overall 4.0 >= advance threshold (3.5) -> a real
    # (unmocked) coordinator recommendation.
    assert state.recommendation is not None
    assert state.recommendation.kind == "advance"

    assert len(save_calls) == 1
    saved_session_id, saved_payload = save_calls[0]
    assert saved_session_id == "sid"
    assert saved_payload["pipeline_status"] == "ready"
    assert saved_payload["scout_findings"] == [{"topic": "news", "summary": "No recent press.", "source_url": None}]
    assert saved_payload["scorecard"]["overall"] == 4.0
    assert saved_payload["recommendation"]["kind"] == "advance"
    # Legacy fields untouched.
    assert saved_payload["summary"] == "a summary"


async def test_scout_soft_fail_empty_findings_still_reaches_ready(monkeypatch):
    scorecard = make_scorecard({"experience": 3.0}, overall=3.0)

    async def fake_scout_run(state):
        return []  # Scout's own soft-fail contract.

    async def fake_score_interview(turns, rubric, scout_findings):
        assert scout_findings == []
        return scorecard

    save_calls: list[dict] = []

    async def fake_save(session_id, payload):
        save_calls.append(dict(payload))

    monkeypatch.setattr(scout_agent, "run", fake_scout_run)
    monkeypatch.setattr(evaluator_agent, "score_interview", fake_score_interview)
    monkeypatch.setattr(transcript_store, "save", fake_save)

    state = make_state()
    await pipeline.run(state, "sid", make_payload())

    assert state.pipeline_status == "ready"
    assert save_calls[0]["pipeline_status"] == "ready"
    assert save_calls[0]["scout_findings"] == []


async def test_evaluator_raises_leaves_scorecard_and_recommendation_null_but_still_ready(monkeypatch, caplog):
    async def fake_scout_run(state):
        return []

    async def fake_score_interview(turns, rubric, scout_findings):
        raise RuntimeError("scoring exploded")

    def _must_not_run(scorecard, rubric):
        raise AssertionError("coordinator must not run when scoring failed")

    save_calls: list[dict] = []

    async def fake_save(session_id, payload):
        save_calls.append(dict(payload))

    monkeypatch.setattr(scout_agent, "run", fake_scout_run)
    monkeypatch.setattr(evaluator_agent, "score_interview", fake_score_interview)
    monkeypatch.setattr(coordinator_agent, "evaluate_followup", _must_not_run)
    monkeypatch.setattr(transcript_store, "save", fake_save)

    state = make_state()
    with caplog.at_level(logging.WARNING, logger="app.services.pipeline"):
        await pipeline.run(state, "sid", make_payload())

    assert "Holistic scoring failed" in caplog.text
    assert state.pipeline_status == "ready"
    assert state.scorecard is None
    assert state.recommendation is None

    assert len(save_calls) == 1
    assert save_calls[0]["pipeline_status"] == "ready"
    assert save_calls[0]["scorecard"] is None
    assert save_calls[0]["recommendation"] is None


async def test_final_save_failure_marks_failed_and_attempts_resave(monkeypatch):
    async def fake_scout_run(state):
        return []

    scorecard = make_scorecard({"experience": 2.0}, overall=2.0)

    async def fake_score_interview(turns, rubric, scout_findings):
        return scorecard

    save_payloads: list[dict] = []

    async def fake_save(session_id, payload):
        save_payloads.append(dict(payload))
        raise OSError("disk full")

    monkeypatch.setattr(scout_agent, "run", fake_scout_run)
    monkeypatch.setattr(evaluator_agent, "score_interview", fake_score_interview)
    monkeypatch.setattr(transcript_store, "save", fake_save)

    state = make_state()

    await pipeline.run(state, "sid", make_payload())  # must not raise

    assert state.pipeline_status == "failed"
    # The initial "ready" save attempt failed, and a best-effort re-save with
    # pipeline_status "failed" was attempted afterwards.
    assert len(save_payloads) == 2
    assert save_payloads[0]["pipeline_status"] == "ready"
    assert save_payloads[1]["pipeline_status"] == "failed"
    # state.pipeline_status must never have been set to "ready" - a save
    # failure would otherwise flip a transient "ready" back to "failed",
    # visible to a poll tick in between.


async def test_ready_status_only_set_after_successful_save(monkeypatch):
    # state.pipeline_status must flip to "ready" only once the save that
    # carries the "ready" record has actually succeeded - not before, so a
    # save failure can never leave a transient "ready" visible to a poll.
    async def fake_scout_run(state):
        return []

    scorecard = make_scorecard({"experience": 4.0}, overall=4.0)

    async def fake_score_interview(turns, rubric, scout_findings):
        return scorecard

    status_during_save: list[str | None] = []

    async def fake_save(session_id, payload):
        status_during_save.append(state.pipeline_status)

    monkeypatch.setattr(scout_agent, "run", fake_scout_run)
    monkeypatch.setattr(evaluator_agent, "score_interview", fake_score_interview)
    monkeypatch.setattr(transcript_store, "save", fake_save)

    state = make_state()
    await pipeline.run(state, "sid", make_payload())

    # At the moment save() ran, the state was still "evaluating" - the
    # "ready" flip happens only after save() returns successfully.
    assert status_during_save == ["evaluating"]
    assert state.pipeline_status == "ready"


async def test_resave_failure_after_final_save_failure_does_not_raise(monkeypatch, caplog):
    async def fake_scout_run(state):
        return []

    async def fake_score_interview(turns, rubric, scout_findings):
        return make_scorecard({}, overall=None)

    async def fake_save(session_id, payload):
        raise OSError("disk full, always")

    monkeypatch.setattr(scout_agent, "run", fake_scout_run)
    monkeypatch.setattr(evaluator_agent, "score_interview", fake_score_interview)
    monkeypatch.setattr(transcript_store, "save", fake_save)

    state = make_state()
    with caplog.at_level(logging.ERROR, logger="app.services.pipeline"):
        await pipeline.run(state, "sid", make_payload())  # must not raise, even though every save fails

    assert state.pipeline_status == "failed"
    assert "also failed" in caplog.text


async def test_unexpected_exception_mid_run_marks_failed_and_attempts_resave(monkeypatch):
    async def fake_scout_run(state):
        return []

    scorecard = make_scorecard({"experience": 5.0}, overall=5.0)

    async def fake_score_interview(turns, rubric, scout_findings):
        return scorecard

    def _boom(scorecard, rubric):
        raise RuntimeError("coordinator blew up unexpectedly")

    save_payloads: list[dict] = []

    async def fake_save(session_id, payload):
        save_payloads.append(dict(payload))

    monkeypatch.setattr(scout_agent, "run", fake_scout_run)
    monkeypatch.setattr(evaluator_agent, "score_interview", fake_score_interview)
    monkeypatch.setattr(coordinator_agent, "evaluate_followup", _boom)
    monkeypatch.setattr(transcript_store, "save", fake_save)

    state = make_state()

    await pipeline.run(state, "sid", make_payload())  # must not raise

    assert state.pipeline_status == "failed"
    assert len(save_payloads) == 1
    assert save_payloads[0]["pipeline_status"] == "failed"


async def test_enqueue_schedules_run_and_task_is_not_gced(monkeypatch):
    calls: list[tuple] = []

    async def fake_run(state, session_id, payload):
        calls.append((state, session_id, payload))

    monkeypatch.setattr(pipeline, "run", fake_run)

    state = make_state()
    pipeline.enqueue(state, "sid", {"foo": "bar"})

    assert len(pipeline._tasks) == 1
    task = next(iter(pipeline._tasks))

    await task

    assert calls == [(state, "sid", {"foo": "bar"})]
    assert pipeline._tasks == set()
