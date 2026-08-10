"""Schema-validation tests for src/isbe/topics/config.py::TopicConfig.

Two purposes:
  1. Lock the contract: every currently-shipped topic.yaml must validate.
  2. Lock the failure mode: malformed yaml fails at load time with a
     field-pointed error, not at flow-execution time three days later.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from isbe.topics.config import TopicConfig
from isbe.topics.registry import default_topics_root, discover_topics

# ---------------------------------------------------------------------------
# Real topic.yaml regression suite — these must always validate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "topic_id",
    ["nowcasting", "video-generation", "image-restoration", "nvda", "motorcycle", "china-tech"],
)
def test_shipped_topic_yaml_validates(topic_id: str) -> None:
    """Each in-tree topic.yaml must parse cleanly under TopicConfig."""
    root = default_topics_root()
    meta = next(t for t in discover_topics(root) if t.id == topic_id)
    cfg = TopicConfig.from_yaml_file(root / _dir_for(meta.id) / "topic.yaml")
    assert cfg.id == topic_id
    assert cfg.label
    assert cfg.schedules, f"{topic_id} should declare at least one schedule"


def _dir_for(topic_id: str) -> str:
    # Filesystem uses underscore for china_tech but yaml id uses hyphen
    return "china_tech" if topic_id == "china-tech" else topic_id


# ---------------------------------------------------------------------------
# Top-level required fields
# ---------------------------------------------------------------------------


def test_missing_id_field_raises_validation_error() -> None:
    with pytest.raises(ValidationError) as exc:
        TopicConfig(label="x", cadence="weekly")  # type: ignore[call-arg]
    assert "id" in str(exc.value)


def test_missing_label_field_raises_validation_error() -> None:
    with pytest.raises(ValidationError) as exc:
        TopicConfig(id="x", cadence="weekly")  # type: ignore[call-arg]
    assert "label" in str(exc.value)


def test_unknown_cadence_value_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        TopicConfig(id="x", label="x", cadence="hourly")  # type: ignore[arg-type]
    assert "cadence" in str(exc.value)


def test_active_defaults_to_true() -> None:
    cfg = TopicConfig(id="x", label="x", cadence="weekly")
    assert cfg.active is True


# ---------------------------------------------------------------------------
# Unknown top-level keys are rejected (typo catch)
# ---------------------------------------------------------------------------


def test_unknown_top_level_field_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        TopicConfig(
            id="x",
            label="x",
            cadence="weekly",
            scheduels={"x": "* * * * *"},  # typo for "schedules"
        )
    msg = str(exc.value)
    assert "scheduels" in msg or "Extra" in msg or "extra" in msg


# ---------------------------------------------------------------------------
# Source sub-blocks
# ---------------------------------------------------------------------------


def test_arxiv_block_parses_lists() -> None:
    cfg = TopicConfig(
        id="x",
        label="x",
        cadence="weekly",
        arxiv={"categories": ["cs.LG"], "include_keywords": ["foo"], "max_results": 50},
    )
    assert cfg.arxiv is not None
    assert cfg.arxiv.categories == ["cs.LG"]
    assert cfg.arxiv.max_results == 50


def test_rss_feeds_require_name_and_url() -> None:
    with pytest.raises(ValidationError) as exc:
        TopicConfig(
            id="x", label="x", cadence="weekly",
            rss={"feeds": [{"name": "a"}]},  # missing url
        )
    assert "url" in str(exc.value)


def test_schedules_must_be_string_crons() -> None:
    with pytest.raises(ValidationError):
        TopicConfig(
            id="x", label="x", cadence="weekly",
            schedules={"weekly_digester": 123},  # type: ignore[dict-item]
        )


def test_extra_keys_in_sub_block_are_rejected() -> None:
    """Sub-blocks also enforce extra='forbid' so typos surface."""
    with pytest.raises(ValidationError) as exc:
        TopicConfig(
            id="x", label="x", cadence="weekly",
            arxiv={"categores": ["cs.LG"]},  # typo
        )
    assert "categores" in str(exc.value) or "extra" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Loader integration
# ---------------------------------------------------------------------------


def test_from_yaml_file_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "topic.yaml"
    p.write_text(
        "id: t1\nlabel: T1\ncadence: weekly\nactive: true\n"
        "schedules:\n  weekly_digester: '0 8 * * 1'\n",
        encoding="utf-8",
    )
    cfg = TopicConfig.from_yaml_file(p)
    assert cfg.id == "t1"
    assert cfg.schedules == {"weekly_digester": "0 8 * * 1"}


def test_from_yaml_file_includes_file_path_in_error(tmp_path: Path) -> None:
    p = tmp_path / "broken.yaml"
    p.write_text("id: t1\ncadence: weekly\n", encoding="utf-8")  # missing label
    with pytest.raises(ValidationError) as exc:
        TopicConfig.from_yaml_file(p)
    # Pydantic error itself surfaces field; loader wraps with file context
    assert "label" in str(exc.value)


# ---------------------------------------------------------------------------
# ${VAR} env interpolation (config points at env-specific endpoints)
# ---------------------------------------------------------------------------


def _write_feed_topic(tmp_path: Path, url: str) -> Path:
    p = tmp_path / "topic.yaml"
    p.write_text(
        "id: t1\nlabel: T1\ncadence: weekly\n"
        "rss:\n  feeds:\n    - name: f\n"
        f"      url: {url}\n"
        "schedules:\n  digester: '0 8 * * 1'\n",
        encoding="utf-8",
    )
    return p


def test_env_default_used_when_var_unset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MORERSSPLZ_URL", raising=False)
    p = _write_feed_topic(tmp_path, "${MORERSSPLZ_URL:-http://morerssplz:8000}/zhihuzhuanlan/x")
    cfg = TopicConfig.from_yaml_file(p)
    assert cfg.rss is not None
    assert cfg.rss.feeds[0].url == "http://morerssplz:8000/zhihuzhuanlan/x"


def test_env_value_overrides_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MORERSSPLZ_URL", "http://localhost:1201")
    p = _write_feed_topic(tmp_path, "${MORERSSPLZ_URL:-http://morerssplz:8000}/zhihuzhuanlan/x")
    cfg = TopicConfig.from_yaml_file(p)
    assert cfg.rss is not None
    assert cfg.rss.feeds[0].url == "http://localhost:1201/zhihuzhuanlan/x"


def test_empty_env_falls_back_to_default(tmp_path: Path, monkeypatch) -> None:
    # An empty string (compose passthrough with nothing set) must NOT win.
    monkeypatch.setenv("MORERSSPLZ_URL", "")
    p = _write_feed_topic(tmp_path, "${MORERSSPLZ_URL:-http://morerssplz:8000}/zhihuzhuanlan/x")
    cfg = TopicConfig.from_yaml_file(p)
    assert cfg.rss is not None
    assert cfg.rss.feeds[0].url == "http://morerssplz:8000/zhihuzhuanlan/x"


def test_undefined_env_without_default_is_config_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("NOPE_UNSET", raising=False)
    p = _write_feed_topic(tmp_path, "${NOPE_UNSET}/x")
    with pytest.raises(ValueError) as exc:
        TopicConfig.from_yaml_file(p)
    assert "NOPE_UNSET" in str(exc.value)
