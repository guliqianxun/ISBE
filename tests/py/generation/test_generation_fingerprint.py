"""The settings fingerprint of queued tasks, and its compatibility with rows
written by the earlier Rust build."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from generation_helpers import (
    REPORT,
    Request,
    Script,
    count,
    finished,
    make_root,
    papers,
    response,
)

from isbe.core import db, locks, settings
from isbe.generation import GENERATION, Manager, Stages
from isbe.generation.fingerprint import fingerprint, legacy_fingerprint, unchanged
from isbe.generation.worker import SETTINGS_CHANGED

type Open = Callable[..., Manager]
type Start = Callable[[Callable[[int, Request], tuple[int, Any]]], Script]

MAIL = {
    "SMTP_HOST": "smtp.example.org",
    "SMTP_PORT": "465",
    "SMTP_SECURITY": "tls",
    "SMTP_USER": "sender@example.org",
    "SMTP_PASSWORD": "mail-secret-one",
    "EMAIL_TRANSPORT": "zeptomail_api",
    "ZEPTOMAIL_API_URL": "https://api.zeptomail.com/v1.1/email",
    "ZEPTOMAIL_TOKEN": "zepto-secret-one",
    "SMTP_FROM": "reports@example.org",
    "SMTP_FROM_NAME": "Reports",
    "ISBE_EMAIL_DEFAULT_RECIPIENTS": "a@example.org",
}

# Captured from a task queued by the earlier build (POST /api/generation with
# the execution lock held): settings.json, topic file and the `tasks` row it wrote.
LEGACY_TOPIC = (
    "id: papers\nlabel: Papers\ncadence: weekly\nlang: zh\nwindow_days: 14\n"
    "arxiv:\n  categories: [cs.LG, cs.CV]\n  include_keywords: [learning, \"radar echo\"]\n  max_results: 80\n"
    "digest: {max_papers: 12}\nretrieval:\n  intent: Test learning\n  facets: [a, b]\n"
)
LEGACY_SNAPSHOT = (
    '{"arxiv":{"categories":["cs.LG","cs.CV"],"include_keywords":["learning","radar echo"],"max_results":80},'
    '"cadence":"weekly","digest":{"max_papers":12},"id":"papers","label":"Papers","lang":"zh",'
    '"retrieval":{"facets":["a","b"],"intent":"Test learning"},"window_days":14}'
)
LEGACY_SETTINGS = {
    "ISBE_LLM_PROVIDER": "openai",
    "ISBE_LLM_NO_AUTH": "true",
    "ISBE_LLM_BASE_URL": "http://127.0.0.1:9",
    "ISBE_LLM_MODEL_SMART": "first-model",
    "ISBE_LLM_TIMEOUT_S": "2",
    "ISBE_FULLTEXT_EXTRACTOR": "off",
    "ESSAIES_URL": "http://127.0.0.1:9",
    "OPENAI_API_KEY": "sk-not-used",
    "SEARXNG_URL": "http://127.0.0.1:8888/",
}
LEGACY_MAIL = {"SMTP_HOST": "smtp.example.org", "SMTP_PORT": "465", "SMTP_PASSWORD": "mail-secret", "SMTP_FROM": "a@example.org"}
LEGACY_PLAIN_FINGERPRINT = "2246299a23ca682915de2b299aaaac36a420c4f7ee4c6343103e0c2cb1c7ed87"
LEGACY_MAIL_FINGERPRINT = "1a178af7d496921c2788ee60e2f30d37175e740dcc4829ceb1c3360dabbe6687"


def test_generation_search_service_edits_do_not_invalidate_report_tasks() -> None:
    values = {"ISBE_LLM_PROVIDER": "openai"}
    before = fingerprint(values)
    values |= {"SEARXNG_URL": "http://127.0.0.1:8888/", "ISBE_ARTICLE_DNS": "cloudflare"}
    assert fingerprint(values) == before


def test_generation_fingerprints_equal_the_legacy_rows(tmp_path: Path) -> None:
    for extra, expected in (({}, LEGACY_PLAIN_FINGERPRINT), (LEGACY_MAIL, LEGACY_MAIL_FINGERPRINT)):
        data = tmp_path / str(len(extra))
        data.mkdir()
        (data / "settings.json").write_text(json.dumps(LEGACY_SETTINGS | extra))
        values = settings.read(data)
        assert legacy_fingerprint(values) == expected
        assert unchanged(expected, values)
    # Without mail settings the allow-list hash equals the earlier hash: both builds accept the row.
    assert fingerprint(settings.read(tmp_path / "0")) == LEGACY_PLAIN_FINGERPRINT
    assert fingerprint(settings.read(tmp_path / "4")) == LEGACY_PLAIN_FINGERPRINT


def test_generation_topic_snapshot_equals_the_legacy_row(tmp_path: Path, managers: Open) -> None:
    root = tmp_path / "data"
    (root / "topics").mkdir(parents=True)
    (root / "topics/papers.yaml").write_text(LEGACY_TOPIC)
    (root / "settings.json").write_text(json.dumps(LEGACY_SETTINGS | LEGACY_MAIL))
    cli = locks.execution_lock(root)
    manager = managers(root)
    job = manager.submit("local", "papers", "parity-1")
    row = db.connect(root / "isbe.db").execute(
        "SELECT topic_snapshot, settings_fingerprint FROM tasks WHERE id = ?", (job.id,)
    ).fetchone()
    assert row[0] == LEGACY_SNAPSHOT
    assert row[1] == LEGACY_PLAIN_FINGERPRINT  # the mail settings no longer count
    cli.release()


def test_fingerprint_input_is_an_allow_list_without_secrets() -> None:
    assert not set(GENERATION) & settings.SECRETS
    assert set(GENERATION) <= set(settings.KEYS)
    # Everything the earlier build fingerprinted beyond the allow-list is a mail setting.
    from isbe.generation.fingerprint import _LEGACY_EXCLUDED

    assert set(settings.KEYS) - _LEGACY_EXCLUDED - set(GENERATION) == set(MAIL) - {"ISBE_EMAIL_DEFAULT_RECIPIENTS"}
    base = {"ISBE_LLM_PROVIDER": "openai", "ISBE_LLM_MODEL_SMART": "m"}
    for secret in settings.SECRETS:
        assert fingerprint(base | {secret: "rotated-secret"}) == fingerprint(base)
    for name, value in MAIL.items():
        assert fingerprint(base | {name: value}) == fingerprint(base)
    for name in GENERATION:
        assert fingerprint(base | {name: "changed"}) != fingerprint(base), name


def _run_with_mock(script: Start) -> Script:
    def answer(i: int, req: Request) -> tuple[int, Any]:
        return 200, papers() if i % 3 == 0 else response(REPORT if i % 3 == 1 else "[]")

    return script(answer)


def test_mail_setting_changes_do_not_fail_queued_tasks(
    tmp_path: Path, script: Start, managers: Open, stages: Stages
) -> None:
    mock = _run_with_mock(script)
    root = make_root(tmp_path / "data", mock.base)
    settings.save(root, {"SMTP_HOST": "old.example.org", "SMTP_PASSWORD": "old-secret"})
    cli = locks.execution_lock(root)
    manager = managers(root, stages=stages)
    job = manager.submit("local", "papers", "one")
    stored = db.connect(root / "isbe.db").execute("SELECT settings_fingerprint FROM tasks").fetchone()[0]
    # An administrator rotates the mail password and changes every other mail setting.
    settings.save(root, dict(MAIL))
    assert fingerprint(settings.read(root)) == stored
    cli.release()
    manager.wake()
    done = finished(manager, job.id)
    assert (done.state, done.error) == ("succeeded", None)
    assert count(root / "isbe.db", "SELECT count(*) FROM runs WHERE task_id = ?", job.id) == 4


@pytest.mark.parametrize(
    "change",
    [
        {"ISBE_LLM_MODEL_SMART": "new-model"},
        {"ISBE_LLM_BASE_URL": "http://127.0.0.1:10"},
        {"ESSAIES_URL": None},
        {"ARXIV_API_URL": "http://127.0.0.1:11"},
        {"ISBE_FULLTEXT_EXTRACTOR": "llm"},
        {"METRAIL_API_URL": "http://127.0.0.1:12"},
    ],
)
def test_model_and_source_changes_still_fail_queued_tasks(
    tmp_path: Path, managers: Open, change: dict[str, str | None]
) -> None:
    root = make_root(tmp_path / "data")
    cli = locks.execution_lock(root)
    manager = managers(root)
    job = manager.submit("local", "papers", "one")
    settings.save(root, change)
    cli.release()
    manager.wake()
    done = finished(manager, job.id)
    assert (done.state, done.error) == ("failed", SETTINGS_CHANGED)
    assert count(root / "isbe.db", "SELECT count(*) FROM runs") == 0


def _queue_rust_row(root: Path, stored: str) -> str:
    """A task as the earlier build queued it, with its fingerprint."""
    conn = db.connect(root / "isbe.db")
    snapshot = '{"arxiv":{"categories":["cs.LG"],"include_keywords":["learning"]},"cadence":"weekly","id":"papers","label":"Papers"}'
    conn.execute(
        "INSERT INTO tasks (id, owner, topic, request_id, state, stage, created_at, topic_snapshot, settings_fingerprint) "
        "VALUES ('0123456789abcdef0123456789abcdef', 'local', 'papers', 'rust', 'queued', 'queued', ?, ?, ?)",
        (db.now(), snapshot, stored),
    )
    return "0123456789abcdef0123456789abcdef"


def test_legacy_fingerprints_run_when_unchanged_and_fail_when_changed(
    tmp_path: Path, script: Start, managers: Open
) -> None:
    mock = _run_with_mock(script)
    root = make_root(tmp_path / "data", mock.base)
    settings.save(root, {"SMTP_HOST": "smtp.example.org", "SMTP_PASSWORD": "mail-secret"})
    legacy = legacy_fingerprint(settings.read(root))
    assert legacy != fingerprint(settings.read(root))
    cli = locks.execution_lock(root)
    manager = managers(root)
    id = _queue_rust_row(root, legacy)
    cli.release()
    manager.wake()
    assert finished(manager, id).state == "succeeded"

    # A legacy row whose settings changed since fails explicitly, as the earlier
    # build would have failed it (its hash covered the mail settings); it does not wait.
    conn = db.connect(root / "isbe.db")
    conn.execute("DELETE FROM task_requests")
    conn.execute("DELETE FROM tasks")
    cli = locks.execution_lock(root)
    id = _queue_rust_row(root, legacy)
    settings.save(root, {"SMTP_PASSWORD": "rotated"})
    cli.release()
    manager.wake()
    done = finished(manager, id)
    assert (done.state, done.error) == ("failed", SETTINGS_CHANGED)
