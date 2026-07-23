"""Loads and validates the interview configuration from YAML into typed,
frozen dataclasses: the per-domain questionnaires (`data/questionnaires/{domain}.yaml`,
one complete linear question script each) and the single global "Signal Matrix"
rubric (`data/rubric.yaml`).

Everything is validated at load time so a malformed config fails loudly on
first use rather than mid-interview: a questionnaire's declared domain must
match its filename, its `next` pointers must form one acyclic path from the
start node through every node to `END` (no cycles, no orphans), and the rubric's
category weights must sum to 1.0 with non-empty, uniquely-labelled,
in-range `value_options`. Public loaders are memoized module-level singletons
(`get_questionnaire` per domain, `get_rubric`/`list_domains` once) rather than
app.state so tests can use TestClient without the lifespan. Domain ids are
validated against a strict slug pattern before any filesystem access to close
off path traversal, since they double as filenames."""

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
    """One question in a domain's linear script: its `id`, `topic`, the `ask`
    text the Host speaks, the `rubric_categories` this answer informs, the
    `next` node id ("END" to finish), and `max_followups` (how many follow-up
    rounds the Host may spend on it before force-advancing)."""

    id: str
    topic: str
    ask: str
    rubric_categories: list[str]
    next: str  # question id, or "END"
    max_followups: int = 1


@dataclass(frozen=True)
class ValueOption:
    """One categorical choice within a rubric category: a human `label` the
    Evaluator picks (e.g. "Strategic") and the `points` it resolves to."""

    label: str
    points: float


@dataclass(frozen=True)
class RubricCategory:
    """One of the Signal Matrix's scoring categories: its `id`, display `name`,
    `weight` in the overall score (all weights sum to 1.0), a `description`
    guiding the Evaluator, and the fixed `value_options` it must choose from."""

    id: str
    name: str
    weight: float
    description: str
    value_options: list[ValueOption]


@dataclass(frozen=True)
class Questionnaire:
    """A fully-parsed, validated domain script: the `domain` id, its display
    `title`, and its `nodes` keyed by id in file (insertion) order - the first
    entry is the interview's start node."""

    domain: str
    title: str
    nodes: dict[str, QuestionNode]


def load_questionnaire(path: Path) -> Questionnaire:
    """Parse and validate one questionnaire YAML into a `Questionnaire`.

    Reads the file, builds a `QuestionNode` per entry (preserving file order),
    and runs `_validate_questionnaire` on the result. Raises `ValueError` if the
    declared `domain` doesn't match the filename stem or the script fails
    validation (bad pointer, cycle, or unreachable node)."""
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
    """Enforce the linear-script invariants, raising `ValueError` on any breach.

    Checks that there is at least one node, that every `next` points to a real
    node or "END", and that following `next` from the start node reaches "END"
    while visiting every node exactly once - i.e. no cycles and no unreachable
    orphans. A fixed linear script has exactly one path through all its nodes."""
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
    """Parse and validate the Signal Matrix rubric YAML into `RubricCategory`
    objects keyed by id. Runs `_validate_rubric` on the result, so it raises
    `ValueError` if the weights don't sum to 1.0 or a category's value options
    are missing, duplicated, or out of range."""
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
    """Enforce the rubric invariants, raising `ValueError` on any breach: the
    category weights must sum to 1.0 (within a small tolerance), and every
    category must have at least one value option, no duplicate option labels,
    and every option's points in [0, 100]."""
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
    """Resolve a configured path against the backend package root when it is
    relative, so the default questionnaire/rubric locations work regardless of
    the process's current working directory. Absolute paths pass through."""
    path = Path(path_str)
    return path if path.is_absolute() else _BACKEND_ROOT / path


# Lazy module-level singletons (no app.state: tests use TestClient without
# lifespan). Loaded on first use, cached for the life of the process - one
# cache entry per domain.


def _validate_domain_slug(domain: str) -> None:
    """Reject any domain id that isn't a simple `[a-z0-9_]` slug, raising
    `KeyError`. Runs before the id is ever joined into a filesystem path, so a
    traversal attempt like "../secrets" can't reach disk."""
    if not _DOMAIN_SLUG_RE.fullmatch(domain):
        raise KeyError(f"invalid domain: {domain!r}")


def _questionnaire_path(domain: str) -> Path:
    """The resolved path to a domain's questionnaire file
    (`{questionnaires_dir}/{domain}.yaml`)."""
    return _resolve_path(settings.questionnaires_dir) / f"{domain}.yaml"


@lru_cache(maxsize=None)
def get_questionnaire(domain: str) -> dict[str, QuestionNode]:
    """Return a domain's question nodes keyed by id, loaded and cached per
    domain for the life of the process. Validates the slug first, then raises
    `KeyError` if no questionnaire file exists for that domain."""
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


# Rough seconds one substantive question costs (ask + answer + ack) and the
# time held back for the closing, used by build_question_plan to size a
# clocked interview's script. Plain constants, not env knobs.
QUESTION_SECONDS_BUDGET = 40
CLOSING_RESERVE_SECONDS = 30


def build_question_plan(domain: str, max_session_seconds: int | None = None) -> list[str]:
    """The ordered node ids one interview will actually ask.

    Without a clock (`max_session_seconds` None - dev tier, chat mode) the
    plan is simply every node in script order. With a clock (prod tier), only
    the top-K substantive questions fit: K = max(1, round((seconds -
    CLOSING_RESERVE_SECONDS) / QUESTION_SECONDS_BUDGET)), chosen by rubric
    weight (a node's weight is the max weight of its `rubric_categories`;
    ties and the final ordering keep script order) - so a short session asks
    the highest-signal questions and skipped categories simply go unscored
    (the Evaluator's renormalization already handles that). The closing node
    (the one pointing at END) is always kept and never counts against K."""
    nodes = list(get_questionnaire(domain).values())
    substantive = [node for node in nodes if node.next != "END"]
    closing = [node for node in nodes if node.next == "END"]

    if max_session_seconds is not None:
        k = max(1, round((max_session_seconds - CLOSING_RESERVE_SECONDS) / QUESTION_SECONDS_BUDGET))
        if k < len(substantive):
            rubric = get_rubric()

            def weight(node: QuestionNode) -> float:
                return max(
                    (rubric[c].weight for c in node.rubric_categories if c in rubric),
                    default=0.0,
                )

            indexed = sorted(enumerate(substantive), key=lambda pair: (-weight(pair[1]), pair[0]))
            kept = sorted(index for index, _ in indexed[:k])
            substantive = [substantive[index] for index in kept]

    return [node.id for node in substantive + closing]


@lru_cache(maxsize=1)
def get_rubric() -> dict[str, RubricCategory]:
    """Return the global Signal Matrix rubric keyed by category id, loaded from
    `settings.rubric_path` and cached once for the life of the process."""
    return load_rubric(_resolve_path(settings.rubric_path))
