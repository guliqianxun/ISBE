from datetime import datetime
from unittest.mock import MagicMock, patch

from isbe.observability.runs import topic_run


def test_topic_run_records_ok_on_success():
    fake_session = MagicMock()
    fake_session.__enter__ = MagicMock(return_value=fake_session)
    fake_session.__exit__ = MagicMock(return_value=False)
    with patch(
        "isbe.observability.runs.make_session_factory", return_value=lambda: fake_session
    ):
        with topic_run("nowcasting", "test-flow") as run:
            run.payload["sample"] = "value"
    assert fake_session.add.call_count == 1
    added = fake_session.add.call_args[0][0]
    assert added.topic_id == "nowcasting"
    assert added.flow_name == "test-flow"
    assert added.status == "ok"
    assert added.payload == {"sample": "value"}
    assert isinstance(added.started_at, datetime)
    assert isinstance(added.finished_at, datetime)


def test_topic_run_records_failed_on_exception():
    fake_session = MagicMock()
    fake_session.__enter__ = MagicMock(return_value=fake_session)
    fake_session.__exit__ = MagicMock(return_value=False)
    with patch(
        "isbe.observability.runs.make_session_factory", return_value=lambda: fake_session
    ):
        try:
            with topic_run("nowcasting", "test-flow"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
    added = fake_session.add.call_args[0][0]
    assert added.status == "failed"
    assert "boom" in added.payload.get("error", "")
