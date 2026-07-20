from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import settings

# Backend package root (liveAvatar/backend/), so the relative default
# questionnaire/rubric paths resolve regardless of the process CWD.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class QuestionNode:
    id: str
    topic: str
    ask: str
    rubric_categories: list[str]
    next: str  # question id, or "END"
    max_followups: int = 1


@dataclass(frozen=True)
class RubricCategory:
    id: str
    name: str
    weight: float
    description: str


def load_questionnaire(path: Path) -> dict[str, QuestionNode]:
    raw = yaml.safe_load(Path(path).read_text())
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
    return nodes


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
        )
        for entry in raw["categories"]
    }
    _validate_rubric(categories)
    return categories


def _validate_rubric(categories: dict[str, RubricCategory]) -> None:
    total_weight = sum(category.weight for category in categories.values())
    if abs(total_weight - 1.0) > 0.01:
        raise ValueError(f"rubric weights must sum to 1.0 (got {total_weight})")


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else _BACKEND_ROOT / path


# Lazy module-level singletons (no app.state: tests use TestClient without
# lifespan). Loaded on first use, cached for the life of the process.


@lru_cache(maxsize=1)
def get_questionnaire() -> dict[str, QuestionNode]:
    return load_questionnaire(_resolve_path(settings.questionnaire_path))


def get_start_node_id() -> str:
    """The interview's first question: the first node in the questionnaire
    file (dicts preserve insertion order)."""
    return next(iter(get_questionnaire()))


@lru_cache(maxsize=1)
def get_rubric() -> dict[str, RubricCategory]:
    return load_rubric(_resolve_path(settings.rubric_path))
