from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from typer.testing import CliRunner

from isbe.cli.main import app

runner = CliRunner()


class _FakeScalars:
    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return list(self._items)

    def first(self):
        return self._items[0] if self._items else None


class _FakeSession:
    """Tiny SQLAlchemy Session-like stub.

    Each call to `scalars(stmt)` / `scalar(stmt)` returns from `_responses`
    in FIFO order.  We don't try to inspect the statement — we just need to
    match the call sequence that `status()` issues per topic
    (collect, digest, fail_count, last_art).
    """

    def __init__(self, responses):
        self._responses = list(responses)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def scalars(self, stmt):
        r = self._responses.pop(0)
        return _FakeScalars(r if isinstance(r, list) else [r] if r is not None else [])

    def scalar(self, stmt):
        return self._responses.pop(0)


def _mk_run(flow_name, status, started_at):
    return SimpleNamespace(
        flow_name=flow_name, status=status, started_at=started_at, id=uuid4()
    )


def _mk_art(topic_id, period_label, created_at):
    return SimpleNamespace(
        id=uuid4(), topic_id=topic_id, period_label=period_label, created_at=created_at
    )


def test_status_renders_table_with_one_row_per_active_topic(monkeypatch, tmp_path):
    # Force mirror to an empty tmp dir so the filesystem fallback returns None
    # — DB rows still drive output.
    now = datetime.now(UTC)

    topics = [
        SimpleNamespace(id="nowcasting", label="临近降水", cadence="weekly", active=True),
        SimpleNamespace(id="nvda", label="NVDA", cadence="daily_after_close", active=True),
        SimpleNamespace(id="inactive-x", label="inactive", cadence="weekly", active=False),
    ]

    # per active topic: scalars(collect)→[run], scalars(digest)→[run],
    #                   scalar(fail_count)→int, scalars(art)→[art]
    nowcasting_responses = [
        _mk_run("arxiv-collector", "ok", now - timedelta(hours=2)),
        _mk_run("weekly-digester", "ok", now - timedelta(days=4)),
        0,
        _mk_art("nowcasting", "2026-W19", now - timedelta(days=4)),
    ]
    nvda_responses = [
        _mk_run("nvda-prices-collector", "ok", now - timedelta(hours=1)),
        _mk_run("nvda-daily-digester", "ok", now - timedelta(hours=8)),
        1,
        _mk_art("nvda", "2026-05-11", now - timedelta(hours=8)),
    ]
    session = _FakeSession(nowcasting_responses + nvda_responses)
    fake_session_factory = MagicMock(return_value=session)

    with patch("isbe.cli.status_cmd.discover_topics", return_value=topics), \
         patch("isbe.cli.status_cmd.make_session_factory", return_value=fake_session_factory):
        result = runner.invoke(
            app, ["status", "--mirror", str(tmp_path), "--days", "7"]
        )

    assert result.exit_code == 0, result.output
    assert "TOPIC" in result.output
    assert "FAIL/7d" in result.output
    assert "nowcasting" in result.output
    assert "nvda" in result.output
    assert "inactive-x" not in result.output
    assert "2026-W19" in result.output
    assert "2026-05-11" in result.output


def test_status_handles_no_active_topics(monkeypatch, tmp_path):
    with patch("isbe.cli.status_cmd.discover_topics", return_value=[]):
        result = runner.invoke(app, ["status", "--mirror", str(tmp_path)])
    assert result.exit_code == 0
    assert "no active topics" in result.output


def test_status_filesystem_fallback_when_no_db_artifact(monkeypatch, tmp_path):
    topics = [SimpleNamespace(id="solo", label="solo", cadence="weekly", active=True)]
    # write a local artifact file so the fs-fallback path lights up
    period_dir = tmp_path / "solo" / "2026-W20"
    period_dir.mkdir(parents=True)
    (period_dir / "2026-W20-abcd1234.md").write_text("body", encoding="utf-8")

    responses = [
        None,         # last_collect → empty
        None,         # last_digest → empty
        0,            # fail_count
        None,         # last_art → None (no DB row)
    ]
    session = _FakeSession(responses)
    fake_session_factory = MagicMock(return_value=session)

    with patch("isbe.cli.status_cmd.discover_topics", return_value=topics), \
         patch("isbe.cli.status_cmd.make_session_factory", return_value=fake_session_factory):
        result = runner.invoke(app, ["status", "--mirror", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "solo" in result.output
    assert "2026-W20 (fs only)" in result.output
