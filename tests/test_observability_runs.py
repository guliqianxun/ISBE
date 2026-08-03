from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

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


def test_persist_failure_does_not_mask_flow_exception(caplog):
    """DB down while the flow also failed: the flow's exception must propagate,
    not the persist error (the old finally-block replaced it)."""
    with patch(
        "isbe.observability.runs._persist_run", side_effect=ConnectionError("db down")
    ):
        with pytest.raises(ValueError, match="real failure"):
            with topic_run("nowcasting", "test-flow"):
                raise ValueError("real failure")
    assert any("persist failed" in r.message for r in caplog.records)


def test_persist_failure_raises_when_flow_succeeded():
    with patch(
        "isbe.observability.runs._persist_run", side_effect=ConnectionError("db down")
    ):
        with pytest.raises(ConnectionError, match="db down"):
            with topic_run("nowcasting", "test-flow"):
                pass
