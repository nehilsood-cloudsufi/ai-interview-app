from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import settings

# Backend package root (liveAvatar/backend/), so the relative default
# questionnaire/rubric paths resolve regardless of the process CWD.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Branch:
    signal: str  # "default" is reserved as the else-branch signal
    next: str  # question id, or "END"


@dataclass(frozen=True)
class QuestionNode:
    id: str
    topic: str
    ask: str
    rubric_categories: list[str]
    branches: list[Branch]
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
        branches = [Branch(signal=b["signal"], next=b["next"]) for b in entry["branches"]]
        node = QuestionNode(
            id=entry["id"],
            topic=entry["topic"],
            ask=entry["ask"].strip(),
            rubric_categories=entry.get("rubric_categories", []),
            branches=branches,
            max_followups=entry.get("max_followups", 1),
        )
        nodes[node.id] = node

    _validate_questionnaire(nodes)
    return nodes


def _validate_questionnaire(nodes: dict[str, QuestionNode]) -> None:
    if "verify_identity" not in nodes:
        raise ValueError("questionnaire must define a 'verify_identity' start node")

    for node in nodes.values():
        if not node.branches:
            raise ValueError(f"question '{node.id}' has no branches")

        signals = {branch.signal for branch in node.branches}
        all_end = all(branch.next == "END" for branch in node.branches)
        if "default" not in signals and not all_end:
            raise ValueError(f"question '{node.id}' is missing a 'default' branch")

        for branch in node.branches:
            if branch.next != "END" and branch.next not in nodes:
                raise ValueError(f"question '{node.id}' branches to unknown question '{branch.next}'")


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


@lru_cache(maxsize=1)
def get_rubric() -> dict[str, RubricCategory]:
    return load_rubric(_resolve_path(settings.rubric_path))
