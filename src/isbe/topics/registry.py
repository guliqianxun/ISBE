from pathlib import Path

import yaml

from isbe.topics.base import TopicMetadata


def discover_topics(root: Path) -> list[TopicMetadata]:
    """Scan root for <id>/topic.yaml files."""
    if not root.exists():
        return []
    out: list[TopicMetadata] = []
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest = sub / "topic.yaml"
        if not manifest.exists():
            continue
        raw = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        out.append(
            TopicMetadata(
                id=raw["id"],
                label=raw["label"],
                cadence=raw["cadence"],
                active=bool(raw.get("active", True)),
            )
        )
    return out


def default_topics_root() -> Path:
    """Return the in-tree topics package dir (src/isbe/topics)."""
    return Path(__file__).parent
