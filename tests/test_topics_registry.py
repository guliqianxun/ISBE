from pathlib import Path

import pytest

from isbe.topics.registry import discover_topics


def test_discover_topics_reads_yaml(tmp_path: Path):
    pkg = tmp_path / "topics_pkg"
    (pkg / "alpha").mkdir(parents=True)
    (pkg / "alpha" / "topic.yaml").write_text(
        "id: alpha\nlabel: Alpha\ncadence: weekly\nactive: true\n",
        encoding="utf-8",
    )
    (pkg / "beta").mkdir()
    (pkg / "beta" / "topic.yaml").write_text(
        "id: beta\nlabel: Beta\ncadence: daily_after_close\nactive: false\n",
        encoding="utf-8",
    )
    (pkg / "_not_a_topic").mkdir()  # 没 topic.yaml，跳过
    topics = discover_topics(pkg)
    ids = {t.id for t in topics}
    assert ids == {"alpha", "beta"}
    alpha = next(t for t in topics if t.id == "alpha")
    assert alpha.cadence == "weekly"
    assert alpha.active is True


def test_discover_topics_empty_when_no_dir(tmp_path: Path):
    assert discover_topics(tmp_path / "missing") == []
