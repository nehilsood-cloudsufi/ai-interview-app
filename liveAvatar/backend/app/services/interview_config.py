import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import settings

# Backend package root (liveAvatar/backend/), so the relative default
# questionnaire/rubric paths resolve regardless of the process CWD.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Domain ids double as filenames ({domain}.yaml) and URL/query values, so they
# are restricted to simple slugs - this is checked BEFORE any filesystem
# access, closing off path-traversal tricks like "../secrets" or "a/b".
_DOMAIN_SLUG_RE = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True)
class QuestionNode:
    id: str
    topic: str
    ask: str
    rubric_categories: list[str]
    next: str  # question id, or "END"
    max_followups: int = 1


@dataclass(frozen=True)
class ValueOption:
    label: str
    points: float


@dataclass(frozen=True)
class RubricCategory:
    id: str
    name: str
    weight: float
    description: str
    value_options: list[ValueOption]


@dataclass(frozen=True)
class Questionnaire:
    domain: str
    title: str
    nodes: dict[str, QuestionNode]


def load_questionnaire(path: Path) -> Questionnaire:
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    domain = raw["domain"]
    title = raw["title"]
    if domain != path.stem:
        raise ValueError(f"questionnaire domain '{domain}' does not match filename '{path.stem}.yaml'")

    nodes: dict[str, QuestionNode] = {}
    for entry in raw["questions"]:
        node = QuestionNode(
            id=entry["id"],
            topic=entry["topic"],
            ask=entry["ask"].strip(),
            rubric_categories=entry.get("rubric_categories", []),
            next=entry["next"],
            max_followups=entry.get("max_followups", 1),
        )
        nodes[node.id] = node

    _validate_questionnaire(nodes)
    return Questionnaire(domain=domain, title=title, nodes=nodes)


def _validate_questionnaire(nodes: dict[str, QuestionNode]) -> None:
    if not nodes:
        raise ValueError("questionnaire must define at least one question")

    for node in nodes.values():
        if node.next != "END" and node.next not in nodes:
            raise ValueError(f"question '{node.id}' points to unknown question '{node.next}'")

    # The chain from the start node must reach END with no cycles/orphans -
    # a fixed linear script has exactly one path through every node.
    start_id = next(iter(nodes))
    visited: set[str] = set()
    current = start_id
    while current != "END":
        if current in visited:
            raise ValueError(f"questionnaire has a cycle involving question '{current}'")
        visited.add(current)
        current = nodes[current].next

    if visited != set(nodes):
        orphans = set(nodes) - visited
        raise ValueError(f"questionnaire has unreachable question(s): {sorted(orphans)}")


def load_rubric(path: Path) -> dict[str, RubricCategory]:
    raw = yaml.safe_load(Path(path).read_text())
    categories = {
        entry["id"]: RubricCategory(
            id=entry["id"],
            name=entry["name"],
            weight=entry["weight"],
            description=entry["description"].strip(),
            value_options=[
                ValueOption(label=option["label"], points=option["points"])
                for option in entry["value_options"]
            ],
        )
        for entry in raw["categories"]
    }
    _validate_rubric(categories)
    return categories


def _validate_rubric(categories: dict[str, RubricCategory]) -> None:
    total_weight = sum(category.weight for category in categories.values())
    if abs(total_weight - 1.0) > 0.01:
        raise ValueError(f"rubric weights must sum to 1.0 (got {total_weight})")

    for category in categories.values():
        if not category.value_options:
            raise ValueError(f"rubric category '{category.id}' must define at least one value_option")

        labels = [option.label for option in category.value_options]
        if len(labels) != len(set(labels)):
            raise ValueError(f"rubric category '{category.id}' has duplicate value_option labels")

        for option in category.value_options:
            if not 0 <= option.points <= 100:
                raise ValueError(
                    f"rubric category '{category.id}' value_option '{option.label}' "
                    f"points must be in [0, 100] (got {option.points})"
                )


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else _BACKEND_ROOT / path


# Lazy module-level singletons (no app.state: tests use TestClient without
# lifespan). Loaded on first use, cached for the life of the process - one
# cache entry per domain.


def _validate_domain_slug(domain: str) -> None:
    if not _DOMAIN_SLUG_RE.fullmatch(domain):
        raise KeyError(f"invalid domain: {domain!r}")


def _questionnaire_path(domain: str) -> Path:
    return _resolve_path(settings.questionnaires_dir) / f"{domain}.yaml"


@lru_cache(maxsize=None)
def get_questionnaire(domain: str) -> dict[str, QuestionNode]:
    _validate_domain_slug(domain)
    path = _questionnaire_path(domain)
    if not path.is_file():
        raise KeyError(f"unknown domain: {domain!r}")
    return load_questionnaire(path).nodes


def get_start_node_id(domain: str) -> str:
    """The interview's first question for this domain: the first node in
    the domain's questionnaire file (dicts preserve insertion order)."""
    return next(iter(get_questionnaire(domain)))


@lru_cache(maxsize=1)
def list_domains() -> list[tuple[str, str]]:
    """(domain_id, title) pairs for every questionnaire file, sorted by id
    for a stable dropdown order."""
    directory = _resolve_path(settings.questionnaires_dir)
    domains = []
    for path in directory.glob("*.yaml"):
        raw = yaml.safe_load(path.read_text())
        domains.append((raw["domain"], raw["title"]))
    return sorted(domains, key=lambda pair: pair[0])


@lru_cache(maxsize=1)
def get_rubric() -> dict[str, RubricCategory]:
    return load_rubric(_resolve_path(settings.rubric_path))
