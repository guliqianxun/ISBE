from pathlib import Path

import yaml
from pydantic import ValidationError

from isbe.topics.base import TopicMetadata
from isbe.topics.config import TopicConfig


def _topic_dirs(root: Path):
    """Yield candidate topic directories (skip private _shared/ and __pycache__)."""
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        if sub.name.startswith("_") or sub.name.startswith("."):
            continue
        if (sub / "topic.yaml").exists():
            yield sub


def _validated(path: Path) -> TopicConfig:
    """Parse + validate one topic.yaml, attaching file context to errors."""
    try:
        return TopicConfig.from_yaml_file(path)
    except ValidationError as e:
        raise ValueError(f"invalid topic config at {path}:\n{e}") from e


def discover_topics(root: Path) -> list[TopicMetadata]:
    """Scan root for <id>/topic.yaml files. Raises on any malformed yaml."""
    if not root.exists():
        return []
    out: list[TopicMetadata] = []
    for sub in _topic_dirs(root):
        cfg = _validated(sub / "topic.yaml")
        out.append(
            TopicMetadata(id=cfg.id, label=cfg.label, cadence=cfg.cadence, active=cfg.active)
        )
    return out


def load_topic_config(root: Path, topic_id: str) -> dict:
    """Return the full topic.yaml content as a dict for the given topic_id.

    Kept dict-shaped for backwards compatibility with existing call sites;
    validation still runs so a malformed yaml fails at load time.
    Prefer load_topic_config_typed() for new code.
    """
    return load_topic_config_typed(root, topic_id).model_dump(exclude_none=True)


def load_topic_config_typed(root: Path, topic_id: str) -> TopicConfig:
    """Typed access to a topic's config. Raises if topic_id is unknown or yaml invalid."""
    for sub in _topic_dirs(root):
        # Avoid full pydantic parse just to filter by id: cheap yaml read first.
        raw = yaml.safe_load((sub / "topic.yaml").read_text(encoding="utf-8")) or {}
        if raw.get("id") == topic_id:
            return _validated(sub / "topic.yaml")
    raise KeyError(f"topic {topic_id} not found under {root}")


def default_topics_root() -> Path:
    """Return the in-tree topics package dir (src/isbe/topics)."""
    return Path(__file__).parent
