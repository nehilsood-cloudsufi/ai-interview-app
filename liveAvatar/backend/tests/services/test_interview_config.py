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
                "id": "verify_identity",
                "topic": "identity_verification",
                "ask": "Confirm the vendor's details.",
                "rubric_categories": [],
                "branches": [{"signal": "default", "next": "closing"}],
            },
            {
                "id": "closing",
                "topic": "closing",
                "ask": "Thank the vendor for their time.",
                "rubric_categories": [],
                "max_followups": 0,
                "branches": [{"signal": "finished", "next": "END"}],
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

    assert set(nodes) == {"verify_identity", "closing"}
    assert nodes["verify_identity"].branches[0].next == "closing"
    assert nodes["closing"].max_followups == 0
    assert nodes["closing"].branches[0].next == "END"


def test_load_questionnaire_missing_start_node_raises(tmp_path):
    data = _valid_questionnaire_data()
    data["questions"] = [q for q in data["questions"] if q["id"] != "verify_identity"]
    path = _write_yaml(tmp_path / "q.yaml", data)

    with pytest.raises(ValueError, match="verify_identity"):
        load_questionnaire(path)


def test_load_questionnaire_unknown_branch_target_raises(tmp_path):
    data = _valid_questionnaire_data()
    data["questions"][0]["branches"] = [{"signal": "default", "next": "nonexistent_question"}]
    path = _write_yaml(tmp_path / "q.yaml", data)

    with pytest.raises(ValueError, match="unknown question"):
        load_questionnaire(path)


def test_load_questionnaire_missing_default_branch_raises(tmp_path):
    data = _valid_questionnaire_data()
    # Real branching (more than one live signal) without a "default" catch-all.
    data["questions"][0]["branches"] = [
        {"signal": "mentions_ai_ml", "next": "closing"},
        {"signal": "mentions_security", "next": "closing"},
    ]
    path = _write_yaml(tmp_path / "q.yaml", data)

    with pytest.raises(ValueError, match="default"):
        load_questionnaire(path)


def test_load_questionnaire_terminal_node_without_default_signal_name_is_allowed(tmp_path):
    # A node whose only branch points straight to END doesn't need the
    # literal signal name "default" - there's nowhere else it could go.
    data = _valid_questionnaire_data()
    path = _write_yaml(tmp_path / "q.yaml", data)

    nodes = load_questionnaire(path)

    assert nodes["closing"].branches[0].signal == "finished"
    assert nodes["closing"].branches[0].next == "END"


def test_load_questionnaire_node_with_no_branches_raises(tmp_path):
    data = _valid_questionnaire_data()
    data["questions"][1]["branches"] = []
    path = _write_yaml(tmp_path / "q.yaml", data)

    with pytest.raises(ValueError, match="no branches"):
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
    assert "verify_identity" in nodes
    assert nodes["verify_identity"].branches


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

    assert "verify_identity" in questionnaire
    assert abs(sum(c.weight for c in rubric.values()) - 1.0) < 0.01
    assert get_questionnaire() is questionnaire
    assert get_rubric() is rubric


def test_get_questionnaire_resolves_relative_to_backend_root(clear_config_cache, tmp_path, monkeypatch):
    # The default paths are relative ("data/questionnaire.yaml"); loading must
    # not depend on the process CWD.
    monkeypatch.chdir(tmp_path)

    assert "verify_identity" in get_questionnaire()
    assert get_rubric()
