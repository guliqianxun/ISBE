from pathlib import Path

from isbe.config import Config, load_config


def test_load_config_reads_uid_from_env(tmp_path: Path, monkeypatch):
    topics_file = tmp_path / "topics.yaml"
    topics_file.write_text("topics: []\n", encoding="utf-8")
    monkeypatch.setenv("ISBE_UID", "alice")
    cfg: Config = load_config(topics_path=topics_file)
    assert cfg.uid == "alice"
    assert cfg.topics == []


def test_load_config_default_uid_is_me(tmp_path: Path, monkeypatch):
    topics_file = tmp_path / "topics.yaml"
    topics_file.write_text("topics: []\n", encoding="utf-8")
    monkeypatch.delenv("ISBE_UID", raising=False)
    cfg: Config = load_config(topics_path=topics_file)
    assert cfg.uid == "me"


def test_load_config_parses_topics(tmp_path: Path):
    topics_file = tmp_path / "topics.yaml"
    topics_file.write_text(
        """topics:
  - id: x
    label: X
    sources: []
""",
        encoding="utf-8",
    )
    cfg = load_config(topics_path=topics_file)
    assert len(cfg.topics) == 1
    assert cfg.topics[0].id == "x"
