from pathlib import Path

import pytest
import yaml

from app.config import settings
from app.services.interview_config import get_questionnaire, get_rubric, load_questionnaire, load_rubric


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data))
    return path


def _valid_questionnaire_data() -> dict:
    return {
        "questions": [
            {
                "id": "company_overview",
                "topic": "company_overview",
                "ask": "Ask for a brief overview of the company.",
                "rubric_categories": [],
                "next": "closing",
            },
            {
                "id": "closing",
                "topic": "closing",
                "ask": "Thank the vendor for their time.",
                "rubric_categories": [],
                "max_followups": 0,
                "next": "END",
            },
        ]
    }


def _valid_rubric_data() -> dict:
    return {
        "categories": [
            {"id": "experience", "name": "Experience", "weight": 0.5, "description": "desc"},
            {"id": "capability", "name": "Capability", "weight": 0.5, "description": "desc"},
        ]
    }


def test_load_questionnaire_valid(tmp_path):
    path = _write_yaml(tmp_path / "q.yaml", _valid_questionnaire_data())

    nodes = load_questionnaire(path)

    assert set(nodes) == {"company_overview", "closing"}
    assert nodes["company_overview"].next == "closing"
    assert nodes["closing"].max_followups == 0
    assert nodes["closing"].next == "END"


def test_load_questionnaire_empty_raises(tmp_path):
    data = _valid_questionnaire_data()
    data["questions"] = []
    path = _write_yaml(tmp_path / "q.yaml", data)

    with pytest.raises(ValueError, match="at least one question"):
        load_questionnaire(path)


def test_load_questionnaire_unknown_next_target_raises(tmp_path):
    data = _valid_questionnaire_data()
    data["questions"][0]["next"] = "nonexistent_question"
    path = _write_yaml(tmp_path / "q.yaml", data)

    with pytest.raises(ValueError, match="unknown question"):
        load_questionnaire(path)


def test_load_questionnaire_orphan_node_raises(tmp_path):
    # A third node exists but nothing in the chain from the start node ever
    # points to it - unreachable, which a fixed linear script must not allow.
    data = _valid_questionnaire_data()
    data["questions"].append(
        {
            "id": "orphan",
            "topic": "orphan",
            "ask": "Never reached.",
            "rubric_categories": [],
            "next": "END",
        }
    )
    path = _write_yaml(tmp_path / "q.yaml", data)

    with pytest.raises(ValueError, match="unreachable"):
        load_questionnaire(path)


def test_load_questionnaire_cycle_raises(tmp_path):
    data = _valid_questionnaire_data()
    # closing now points back at company_overview instead of END - a cycle.
    data["questions"][1]["next"] = "company_overview"
    path = _write_yaml(tmp_path / "q.yaml", data)

    with pytest.raises(ValueError, match="cycle"):
        load_questionnaire(path)


def test_load_rubric_valid(tmp_path):
    path = _write_yaml(tmp_path / "r.yaml", _valid_rubric_data())

    categories = load_rubric(path)

    assert set(categories) == {"experience", "capability"}
    assert categories["experience"].weight == 0.5
    assert categories["experience"].name == "Experience"


def test_load_rubric_weights_not_summing_to_one_raises(tmp_path):
    data = _valid_rubric_data()
    data["categories"][0]["weight"] = 0.3
    path = _write_yaml(tmp_path / "r.yaml", data)

    with pytest.raises(ValueError, match="sum to 1.0"):
        load_rubric(path)


def test_load_rubric_weights_within_tolerance_is_allowed(tmp_path):
    data = _valid_rubric_data()
    data["categories"][0]["weight"] = 0.505
    data["categories"][1]["weight"] = 0.495
    path = _write_yaml(tmp_path / "r.yaml", data)

    categories = load_rubric(path)

    assert len(categories) == 2


def test_shipped_questionnaire_loads_and_validates():
    nodes = load_questionnaire(Path(settings.questionnaire_path))
    # The shipped tree starts at company_overview - identity verification was
    # deliberately removed (the intake form is the source of truth).
    assert "verify_identity" not in nodes
    assert next(iter(nodes)) == "company_overview"
    assert nodes["company_overview"].next == "ai_ml_depth"


def test_shipped_rubric_loads_and_weights_sum_to_one():
    categories = load_rubric(Path(settings.rubric_path))
    assert abs(sum(c.weight for c in categories.values()) - 1.0) < 0.01


@pytest.fixture
def clear_config_cache():
    get_questionnaire.cache_clear()
    get_rubric.cache_clear()
    yield
    get_questionnaire.cache_clear()
    get_rubric.cache_clear()


def test_get_questionnaire_and_rubric_are_cached_singletons(clear_config_cache):
    questionnaire = get_questionnaire()
    rubric = get_rubric()

    assert "company_overview" in questionnaire
    assert abs(sum(c.weight for c in rubric.values()) - 1.0) < 0.01
    assert get_questionnaire() is questionnaire
    assert get_rubric() is rubric


def test_get_questionnaire_resolves_relative_to_backend_root(clear_config_cache, tmp_path, monkeypatch):
    # The default paths are relative ("data/questionnaire.yaml"); loading must
    # not depend on the process CWD.
    monkeypatch.chdir(tmp_path)

    assert "company_overview" in get_questionnaire()
    assert get_rubric()
