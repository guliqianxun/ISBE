from pathlib import Path

import yaml

from isbe.topics.base import TopicMetadata


def _topic_dirs(root: Path):
    """Yield candidate topic directories (skip private _shared/ and __pycache__)."""
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        if sub.name.startswith("_") or sub.name.startswith("."):
            continue
        if (sub / "topic.yaml").exists():
            yield sub


def discover_topics(root: Path) -> list[TopicMetadata]:
    """Scan root for <id>/topic.yaml files."""
    if not root.exists():
        return []
    out: list[TopicMetadata] = []
    for sub in _topic_dirs(root):
        raw = yaml.safe_load((sub / "topic.yaml").read_text(encoding="utf-8")) or {}
        out.append(
            TopicMetadata(
                id=raw["id"],
                label=raw["label"],
                cadence=raw["cadence"],
                active=bool(raw.get("active", True)),
            )
        )
    return out


def load_topic_config(root: Path, topic_id: str) -> dict:
    """Return the full topic.yaml content as a dict for the given topic_id."""
    for sub in _topic_dirs(root):
        raw = yaml.safe_load((sub / "topic.yaml").read_text(encoding="utf-8")) or {}
        if raw.get("id") == topic_id:
            return raw
    raise KeyError(f"topic {topic_id} not found under {root}")


def default_topics_root() -> Path:
    """Return the in-tree topics package dir (src/isbe/topics)."""
    return Path(__file__).parent
