import sqlite3
from pathlib import Path

import pytest

from isbe import cli
from isbe.core import settings
from isbe.topics import parse

TOPIC = "id: papers\nlabel: Papers\ncadence: weekly\nlang: en\narxiv:\n  categories: [cs.LG]\n  include_keywords: [learning]\n"


def test_init_creates_the_layout_and_prints_the_path_as_given(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw = f"{tmp_path}/data/"
    assert cli.main(["--data", raw, "init"]) == 0
    assert capsys.readouterr().out == f"ready: {raw}\n"
    for sub in ("topics", "memory", "reports"):
        assert (tmp_path / "data" / sub).is_dir()
    conn = sqlite3.connect(tmp_path / "data" / "isbe.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
    conn.close()
    assert cli.main(["init", "--data", raw]) == 0, "safe to re-run, --data after the command"


def test_topics_lines_match_the_columns(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (data / "topics" / "papers.yaml").write_text(TOPIC)
    (data / "topics" / "later.yaml").write_text(
        "id: later\nlabel: Later\ncadence: daily\nlang: zh\nactive: false\n"
        "schedules:\n  collect: '0 6 * * *'\nrss:\n  feeds: [{name: Example, url: 'https://example.com/feed'}]\n"
    )
    assert cli.main(["--data", str(data), "topics"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "later                  articles daily    zh     collect=0 6 * * *      digest=-            Later  (inactive)",
        "papers                 papers   weekly   en     collect=-              digest=-            Papers",
    ]


def test_account_add_strips_only_the_line_ending(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    password = data / "password"
    password.write_bytes(b"  parity admin password \r\n\n")
    args = ["--data", str(data), "account-add", "admin", "--password-file", str(password), "--admin"]
    assert cli.main(args) == 0
    assert capsys.readouterr().out == "Account created: admin (admin)\n"
    from isbe import auth
    from isbe.core import db

    conn = db.connect(data / "isbe.db")
    user, _ = auth.login(conn, "admin", "  parity admin password ", "127.0.0.1")
    assert user.role == "admin"
    assert cli.main(args) == 1
    assert capsys.readouterr().err == "Error: Username is already in use\n"


def test_explicit_empty_cli_value_does_not_fall_back_to_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ESSAIES_URL", "http://from-env")
    assert cli.configured("", "ESSAIES_URL") is None
    assert cli.configured("  ", "ESSAIES_URL") is None
    assert cli.configured("http://explicit", "ESSAIES_URL") == "http://explicit"
    assert cli.configured(None, "ESSAIES_URL") == settings.get("ESSAIES_URL") == "http://from-env"


def test_select_names_or_every_active_topic() -> None:
    active = parse(TOPIC, "papers")
    idle = parse(TOPIC.replace("papers", "idle") + "active: false\n", "idle")
    assert [t.id for t in cli.select([active, idle], [])] == ["papers"]
    assert [t.id for t in cli.select([active, idle], ["idle"])] == ["idle"]
    with pytest.raises(ValueError, match=r'^no topic "nope" in topics/$'):
        cli.select([active], ["nope"])


def test_each_runs_every_topic_and_counts_failures() -> None:
    topics = [parse(TOPIC, "papers"), parse(TOPIC.replace("papers", "b"), "b")]
    seen: list[str] = []

    def fail_first(t: object) -> bool:
        seen.append(t.id)  # type: ignore[attr-defined]
        return len(seen) > 1

    with pytest.raises(RuntimeError, match="^1 of 2 topics failed$"):
        cli.each(topics, fail_first)
    assert seen == ["papers", "b"]


def test_job_prints_ok_and_failed_lines(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from isbe.core import db

    conn = db.connect(data / "isbe.db")
    t = parse(TOPIC, "papers")

    def ok(_: str, stats: dict[str, object]) -> None:
        stats.update({"new": 3, "found": {"essaies": 3}})

    def bad(_: str, stats: dict[str, object]) -> None:
        try:
            raise OSError("connection refused")
        except OSError as error:
            raise RuntimeError("essaies request failed") from error

    assert cli.job(conn, t, "collect", ok)
    assert not cli.job(conn, t, "triage", bad)
    out = capsys.readouterr()
    assert out.out == 'papers                 collect  ok      {"found":{"essaies":3},"new":3}\n'
    assert out.err == "papers                 triage   failed  essaies request failed: connection refused\n"


def test_errors_print_with_their_cause_chain() -> None:
    def chain(*messages: str) -> BaseException:
        error: BaseException | None = None
        for message in reversed(messages):
            outer = RuntimeError(message)
            outer.__cause__ = error
            error = outer
        assert error is not None
        return error

    assert cli.describe_error(chain("1 of 1 topics failed")) == "Error: 1 of 1 topics failed"
    assert cli.describe_error(chain("open db", "disk full")) == "Error: open db\n\nCaused by:\n    disk full"
    assert cli.describe_error(chain("a", "b", "c")) == "Error: a\n\nCaused by:\n    0: b\n    1: c"


def test_pipeline_commands_hold_the_execution_lock(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from isbe.core import locks

    with locks.execution_lock(data):
        assert cli.main(["--data", str(data), "triage"]) == 1
    assert capsys.readouterr().err.startswith("Error: Another report or CLI operation is running")


def test_import_reports_records_the_directory_as_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / "legacy" / "papers" / "2026-W01"
    legacy.mkdir(parents=True)
    (legacy / "latest.md").write_text("# Legacy weekly\n\n## TL;DR\nImported.\n")
    assert cli.main(["--data", "data", "init"]) == 0
    capsys.readouterr()
    assert cli.main(["--data", "data", "import-reports", "./legacy"]) == 0
    assert capsys.readouterr().out == 'import ok {"beside_own":0,"figures":0,"imported":1}\n'
    conn = sqlite3.connect(tmp_path / "data" / "isbe.db")
    (fingerprint,) = conn.execute("SELECT fingerprint FROM reports").fetchone()
    conn.close()
    assert json.loads(fingerprint) == {"imported_from": "./legacy/papers/2026-W01/latest.md"}
