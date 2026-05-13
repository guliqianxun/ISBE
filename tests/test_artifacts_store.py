from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from isbe.artifacts.store import save_artifact


def _mock_db_and_minio():
    """Returns (minio_mock, session_factory_mock) configured for save_artifact."""
    fake_minio = MagicMock()
    fake_session = MagicMock()
    fake_session_factory = MagicMock(return_value=fake_session)
    fake_session.__enter__ = MagicMock(return_value=fake_session)
    fake_session.__exit__ = MagicMock(return_value=False)
    return fake_minio, fake_session, fake_session_factory


def test_save_artifact_writes_minio_and_pg(monkeypatch, tmp_path):
    monkeypatch.setenv("ISBE_ARTIFACT_MIRROR", str(tmp_path))
    fake_minio, fake_session, fake_session_factory = _mock_db_and_minio()

    with patch("isbe.artifacts.store._get_minio_client", return_value=fake_minio), \
         patch("isbe.artifacts.store.make_session_factory", return_value=fake_session_factory):
        artifact_id = save_artifact(
            topic_id="nowcasting",
            kind="weekly_digest",
            period_label="2026-W19",
            body_markdown="# Test\nbody",
            fingerprint={"facts": [1, 2], "memory": {"a": 1}, "trace_id": "t1"},
            generated_at=datetime(2026, 5, 7, tzinfo=UTC),
        )
    assert artifact_id is not None
    fake_minio.put_object.assert_called_once()
    fake_session.add.assert_called_once()
    fake_session.commit.assert_called_once()


def test_save_artifact_creates_human_readable_name_and_latest(monkeypatch, tmp_path):
    monkeypatch.setenv("ISBE_ARTIFACT_MIRROR", str(tmp_path))
    fake_minio, _, fake_session_factory = _mock_db_and_minio()

    with patch("isbe.artifacts.store._get_minio_client", return_value=fake_minio), \
         patch("isbe.artifacts.store.make_session_factory", return_value=fake_session_factory):
        artifact_id = save_artifact(
            topic_id="nowcasting",
            kind="weekly_digest",
            period_label="2026-W19",
            body_markdown="# Hello",
            fingerprint={},
            generated_at=datetime(2026, 5, 7, tzinfo=UTC),
        )

    period_dir = tmp_path / "nowcasting" / "2026-W19"
    expected_name = f"2026-W19-{artifact_id.hex[:8]}.md"
    assert (period_dir / expected_name).exists()
    assert (period_dir / "latest.md").exists()
    assert (period_dir / "latest.md").read_text(encoding="utf-8") == "# Hello"


def test_save_artifact_rotates_existing_into_history(monkeypatch, tmp_path):
    monkeypatch.setenv("ISBE_ARTIFACT_MIRROR", str(tmp_path))
    fake_minio, _, fake_session_factory = _mock_db_and_minio()

    with patch("isbe.artifacts.store._get_minio_client", return_value=fake_minio), \
         patch("isbe.artifacts.store.make_session_factory", return_value=fake_session_factory):
        first_id = save_artifact(
            topic_id="nowcasting",
            kind="weekly_digest",
            period_label="2026-W19",
            body_markdown="# v1",
            fingerprint={},
            generated_at=datetime(2026, 5, 7, tzinfo=UTC),
        )
        second_id = save_artifact(
            topic_id="nowcasting",
            kind="weekly_digest",
            period_label="2026-W19",
            body_markdown="# v2",
            fingerprint={},
            generated_at=datetime(2026, 5, 8, tzinfo=UTC),
        )

    period_dir = tmp_path / "nowcasting" / "2026-W19"
    history_dir = period_dir / ".history"
    first_name = f"2026-W19-{first_id.hex[:8]}.md"
    second_name = f"2026-W19-{second_id.hex[:8]}.md"

    # The older file should have been moved to .history/
    assert (history_dir / first_name).exists()
    assert not (period_dir / first_name).exists()
    # The newer file plus latest.md remain at top
    assert (period_dir / second_name).exists()
    assert (period_dir / "latest.md").read_text(encoding="utf-8") == "# v2"


def test_save_artifact_rotates_legacy_uuid_named_files(monkeypatch, tmp_path):
    """Legacy dev-period files (naked uuid.md) should also be swept into .history/."""
    monkeypatch.setenv("ISBE_ARTIFACT_MIRROR", str(tmp_path))
    period_dir = tmp_path / "nowcasting" / "2026-W19"
    period_dir.mkdir(parents=True)
    legacy = period_dir / "deadbeef-1111-2222-3333-444455556666.md"
    legacy.write_text("legacy", encoding="utf-8")

    fake_minio, _, fake_session_factory = _mock_db_and_minio()
    with patch("isbe.artifacts.store._get_minio_client", return_value=fake_minio), \
         patch("isbe.artifacts.store.make_session_factory", return_value=fake_session_factory):
        save_artifact(
            topic_id="nowcasting",
            kind="weekly_digest",
            period_label="2026-W19",
            body_markdown="# fresh",
            fingerprint={},
            generated_at=datetime(2026, 5, 9, tzinfo=UTC),
        )

    assert not legacy.exists()
    assert (period_dir / ".history" / legacy.name).exists()
    assert (period_dir / "latest.md").read_text(encoding="utf-8") == "# fresh"
