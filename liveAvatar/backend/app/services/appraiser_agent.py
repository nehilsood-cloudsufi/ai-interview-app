"""Appraiser agent: per-answer LLM scoring plus a deterministic scorecard.

`score_answer` makes one Gemini JSON call (same raw-httpx OpenAI-compatible
pattern as `summary_service`/`host_agent`) to score a completed answer
against the rubric categories attached to that question. The LLM only
proposes scores - all post-processing is done here in code: each score is
clamped to an integer 0-5, category ids outside the question's
`rubric_categories` are dropped, and missing evidence/rationale become "".
Mirroring `summary_service`'s philosophy, it raises on every failure path;
`score_and_store` is the fire-and-forget wrapper that swallows and logs
failures so a scoring hiccup never breaks the interview turn.

`compute_scorecard` is pure, deterministic aggregation (no LLM, no I/O):
per-category means over the collected `AnswerScore`s and an overall that
weights only the categories with data, with their rubric weights
renormalized to sum to 1.0.
"""

import logging
from dataclasses import dataclass

import httpx

from app.config import settings
from app.services.interview_config import QuestionNode, RubricCategory
from app.services.interview_state import AnswerScore, InterviewState
from app.services.llm_json import parse_llm_json

logger = logging.getLogger(__name__)

MIN_SCORE = 0
MAX_SCORE = 5

# Strict structured output for the scoring call (same rationale as
# host_agent._TURN_SCHEMA: prevents the malformed-JSON completions observed
# live). category_scores keys are validated in code against the question's
# rubric_categories, so the schema only pins the value type.
_SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "category_scores": {"type": "object", "additionalProperties": {"type": "integer"}},
        "evidence": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["category_scores", "evidence", "rationale"],
}


@dataclass
class CategoryScore:
    id: str
    name: str
    weight: float
    score: float | None  # None until this category has any data
    evidence: list[str]


@dataclass
class Scorecard:
    categories: list[CategoryScore]  # one per rubric category, rubric order
    overall: float | None  # None until any category has data
    answered_questions: int


def _render_system_content(question: QuestionNode, rubric: dict[str, RubricCategory]) -> str:
    lines = [settings.appraiser_system_prompt, "", "Rubric categories to score:"]
    for category_id in question.rubric_categories:
        category = rubric[category_id]
        lines.append(f"- {category.id} ({category.name}): {category.description}")
    return "\n".join(lines)


def _render_user_content(question: QuestionNode, answer_text: str) -> str:
    return (
        f"Question topic: {question.topic}\n"
        f"Question asked: {question.ask}\n\n"
        f"Vendor's answer:\n{answer_text.strip()}"
    )


def _clamp_score(value: object) -> int:
    return max(MIN_SCORE, min(MAX_SCORE, int(round(float(value)))))  # type: ignore[arg-type]


async def score_answer(
    state: InterviewState,
    question: QuestionNode,
    answer_text: str,
    rubric: dict[str, RubricCategory],
) -> AnswerScore:
    """Score one completed answer with a single Gemini JSON call, append the
    resulting AnswerScore to `state.scores`, and return it. Raises on any
    HTTP/parse failure - the caller decides how to soft-fail."""
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured; cannot score answers.")

    payload = {
        "model": settings.gemini_model,
        "messages": [
            {"role": "system", "content": _render_system_content(question, rubric)},
            {"role": "user", "content": _render_user_content(question, answer_text)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "answer_score", "strict": True, "schema": _SCORE_SCHEMA},
        },
        "reasoning_effort": "low",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{settings.gemini_base_url}chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.gemini_api_key}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        data = response.json()

    parsed = parse_llm_json(data["choices"][0]["message"]["content"])

    category_scores = {
        category_id: _clamp_score(value)
        for category_id, value in (parsed.get("category_scores") or {}).items()
        if category_id in question.rubric_categories
    }
    score = AnswerScore(
        question_id=question.id,
        category_scores=category_scores,
        evidence=str(parsed.get("evidence") or ""),
        rationale=str(parsed.get("rationale") or ""),
    )
    state.scores.append(score)
    return score


async def score_and_store(
    state: InterviewState,
    question: QuestionNode,
    answer_text: str,
    rubric: dict[str, RubricCategory],
) -> None:
    """Fire-and-forget hook target: score the answer, but never raise - a
    failed scoring call just leaves the answer unscored."""
    try:
        await score_answer(state, question, answer_text, rubric)
    except Exception:
        logger.warning(
            "Appraiser scoring failed for interview %s at question %s; answer left unscored.",
            state.interview_id,
            question.id,
            exc_info=True,
        )


def compute_scorecard(scores: list[AnswerScore], rubric: dict[str, RubricCategory]) -> Scorecard:
    """Pure deterministic aggregation of AnswerScores into a Scorecard."""
    categories: list[CategoryScore] = []
    for category in rubric.values():
        scored = [s for s in scores if category.id in s.category_scores]
        values = [s.category_scores[category.id] for s in scored]
        categories.append(
            CategoryScore(
                id=category.id,
                name=category.name,
                weight=category.weight,
                score=round(sum(values) / len(values), 2) if values else None,
                evidence=[s.evidence for s in scored if s.evidence],
            )
        )

    with_data = [c for c in categories if c.score is not None]
    overall = None
    if with_data:
        # Renormalize the weights of the categories that have data to 1.0.
        total_weight = sum(c.weight for c in with_data)
        overall = round(sum(c.score * (c.weight / total_weight) for c in with_data), 2)

    return Scorecard(
        categories=categories,
        overall=overall,
        answered_questions=len({s.question_id for s in scores}),
    )
