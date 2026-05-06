from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from isbe.artifacts.store import save_artifact


def test_save_artifact_writes_minio_and_pg(monkeypatch):
    fake_minio = MagicMock()
    fake_session = MagicMock()
    fake_session_factory = MagicMock(return_value=fake_session)
    fake_session.__enter__ = MagicMock(return_value=fake_session)
    fake_session.__exit__ = MagicMock(return_value=False)

    with patch("isbe.artifacts.store._get_minio_client", return_value=fake_minio), \
         patch("isbe.artifacts.store.make_session_factory", return_value=fake_session_factory):
        artifact_id = save_artifact(
            topic_id="nowcasting",
            kind="weekly_digest",
            period_label="2026-W19",
            body_markdown="# Test\nbody",
            fingerprint={"facts": [1, 2], "memory": {"a": 1}, "trace_id": "t1"},
            generated_at=datetime(2026, 5, 7, tzinfo=timezone.utc),
        )
    assert artifact_id is not None
    fake_minio.put_object.assert_called_once()
    fake_session.add.assert_called_once()
    fake_session.commit.assert_called_once()
