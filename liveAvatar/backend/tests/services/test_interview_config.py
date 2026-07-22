from pathlib import Path

import pytest
import yaml

from app.config import settings
from app.services.interview_config import (
    get_questionnaire,
    get_rubric,
    get_start_node_id,
    list_domains,
    load_questionnaire,
    load_rubric,
)


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data))
    return path


def _valid_questionnaire_data(domain: str = "company_overview", title: str = "Test Domain") -> dict:
    return {
        "domain": domain,
        "title": title,
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
        ],
    }


def _valid_rubric_data() -> dict:
    return {
        "categories": [
            {
                "id": "experience",
                "name": "Experience",
                "weight": 0.5,
                "description": "desc",
                "value_options": [
                    {"label": "Deep", "points": 100},
                    {"label": "Shallow", "points": 40},
                ],
            },
            {
                "id": "capability",
                "name": "Capability",
                "weight": 0.5,
                "description": "desc",
                "value_options": [
                    {"label": "Strong", "points": 100},
                    {"label": "Weak", "points": 20},
                ],
            },
        ]
    }


def test_load_questionnaire_valid(tmp_path):
    path = _write_yaml(tmp_path / "company_overview.yaml", _valid_questionnaire_data())

    questionnaire = load_questionnaire(path)

    assert questionnaire.domain == "company_overview"
    assert questionnaire.title == "Test Domain"
    assert set(questionnaire.nodes) == {"company_overview", "closing"}
    assert questionnaire.nodes["company_overview"].next == "closing"
    assert questionnaire.nodes["closing"].max_followups == 0
    assert questionnaire.nodes["closing"].next == "END"


def test_load_questionnaire_domain_stem_mismatch_raises(tmp_path):
    path = _write_yaml(tmp_path / "some_other_name.yaml", _valid_questionnaire_data(domain="company_overview"))

    with pytest.raises(ValueError, match="does not match filename"):
        load_questionnaire(path)


def test_load_questionnaire_empty_raises(tmp_path):
    data = _valid_questionnaire_data()
    data["questions"] = []
    path = _write_yaml(tmp_path / "company_overview.yaml", data)

    with pytest.raises(ValueError, match="at least one question"):
        load_questionnaire(path)


def test_load_questionnaire_unknown_next_target_raises(tmp_path):
    data = _valid_questionnaire_data()
    data["questions"][0]["next"] = "nonexistent_question"
    path = _write_yaml(tmp_path / "company_overview.yaml", data)

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
    path = _write_yaml(tmp_path / "company_overview.yaml", data)

    with pytest.raises(ValueError, match="unreachable"):
        load_questionnaire(path)


def test_load_questionnaire_cycle_raises(tmp_path):
    data = _valid_questionnaire_data()
    # closing now points back at company_overview instead of END - a cycle.
    data["questions"][1]["next"] = "company_overview"
    path = _write_yaml(tmp_path / "company_overview.yaml", data)

    with pytest.raises(ValueError, match="cycle"):
        load_questionnaire(path)


def test_load_rubric_valid(tmp_path):
    path = _write_yaml(tmp_path / "r.yaml", _valid_rubric_data())

    categories = load_rubric(path)

    assert set(categories) == {"experience", "capability"}
    assert categories["experience"].weight == 0.5
    assert categories["experience"].name == "Experience"
    assert [(o.label, o.points) for o in categories["experience"].value_options] == [
        ("Deep", 100),
        ("Shallow", 40),
    ]
    assert [(o.label, o.points) for o in categories["capability"].value_options] == [
        ("Strong", 100),
        ("Weak", 20),
    ]


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


def test_load_rubric_missing_value_options_key_raises(tmp_path):
    # No `value_options` key at all is a malformed-YAML condition (distinct
    # from an explicit empty list, which is the shape `_validate_rubric`
    # checks) - it fails at parse time with a KeyError.
    data = _valid_rubric_data()
    del data["categories"][0]["value_options"]
    path = _write_yaml(tmp_path / "r.yaml", data)

    with pytest.raises(KeyError, match="value_options"):
        load_rubric(path)


def test_load_rubric_empty_value_options_raises(tmp_path):
    data = _valid_rubric_data()
    data["categories"][0]["value_options"] = []
    path = _write_yaml(tmp_path / "r.yaml", data)

    with pytest.raises(ValueError, match="value_option"):
        load_rubric(path)


def test_load_rubric_duplicate_value_option_labels_raises(tmp_path):
    data = _valid_rubric_data()
    data["categories"][0]["value_options"] = [
        {"label": "Deep", "points": 100},
        {"label": "Deep", "points": 40},
    ]
    path = _write_yaml(tmp_path / "r.yaml", data)

    with pytest.raises(ValueError, match="duplicate"):
        load_rubric(path)


@pytest.mark.parametrize("bad_points", [-1, 100.1, 200])
def test_load_rubric_value_option_points_out_of_range_raises(tmp_path, bad_points):
    data = _valid_rubric_data()
    data["categories"][0]["value_options"][0]["points"] = bad_points
    path = _write_yaml(tmp_path / "r.yaml", data)

    with pytest.raises(ValueError, match=r"\[0, 100\]"):
        load_rubric(path)


def test_shipped_rubric_loads_and_weights_sum_to_one():
    categories = load_rubric(Path(settings.rubric_path))
    assert abs(sum(c.weight for c in categories.values()) - 1.0) < 0.01


# --- Per-domain loading (app.services.interview_config.get_questionnaire) ---


@pytest.fixture
def domains_dir(tmp_path, patch_settings):
    directory = tmp_path / "questionnaires"
    directory.mkdir()
    patch_settings(questionnaires_dir=str(directory))
    return directory


def _seed_domain(directory: Path, domain: str, title: str = "Some Domain") -> Path:
    return _write_yaml(directory / f"{domain}.yaml", _valid_questionnaire_data(domain=domain, title=title))


def test_get_questionnaire_loads_domain_file(domains_dir):
    _seed_domain(domains_dir, "widgets")

    nodes = get_questionnaire("widgets")

    assert set(nodes) == {"company_overview", "closing"}


def test_get_questionnaire_is_cached_per_domain(domains_dir):
    _seed_domain(domains_dir, "widgets")

    first = get_questionnaire("widgets")
    assert get_questionnaire("widgets") is first


def test_get_questionnaire_unknown_domain_raises_key_error(domains_dir):
    with pytest.raises(KeyError):
        get_questionnaire("does_not_exist")


def test_get_questionnaire_rejects_non_slug_domain(domains_dir):
    for bogus in ("../secrets", "a/b", "Weird-Domain", "with space", ""):
        with pytest.raises(KeyError):
            get_questionnaire(bogus)


def test_get_start_node_id_resolves_domain_first_node(domains_dir):
    _seed_domain(domains_dir, "widgets")

    assert get_start_node_id("widgets") == "company_overview"


def test_list_domains_sorted_by_id(domains_dir):
    _seed_domain(domains_dir, "zeta", title="Zeta Domain")
    _seed_domain(domains_dir, "alpha", title="Alpha Domain")

    domains = list_domains()

    assert domains == [("alpha", "Alpha Domain"), ("zeta", "Zeta Domain")]


def test_list_domains_is_cached(domains_dir):
    _seed_domain(domains_dir, "alpha", title="Alpha Domain")

    first = list_domains()
    _seed_domain(domains_dir, "beta", title="Beta Domain")

    assert list_domains() is first


# --- Real shipped questionnaire files under data/questionnaires/ ---

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_all_shipped_questionnaires_load_and_validate():
    directory = _BACKEND_ROOT / "data" / "questionnaires"
    rubric = load_rubric(_BACKEND_ROOT / settings.rubric_path)
    yaml_files = sorted(directory.glob("*.yaml"))

    assert len(yaml_files) == 4

    for path in yaml_files:
        questionnaire = load_questionnaire(path)
        assert questionnaire.domain == path.stem
        assert questionnaire.title
        assert next(iter(questionnaire.nodes)) == "intro"
        for node in questionnaire.nodes.values():
            for category_id in node.rubric_categories:
                assert category_id in rubric, (
                    f"{path.name} question '{node.id}' references unknown rubric category '{category_id}'"
                )
