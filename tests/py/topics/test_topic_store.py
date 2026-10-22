import os
import threading
from pathlib import Path
from typing import Any

import pytest

from isbe.core import settings
from isbe.core.errors import HttpError
from isbe.topics import create, delete, get, list_topics, parse, status, update
from isbe.topics.store import MAX_BYTES
from isbe.topics.yamlio import dump


@pytest.fixture(autouse=True)
def saved(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Settings saved through the console; tests edit the dict to "save" new values."""
    values: dict[str, str] = {}
    monkeypatch.setattr(settings, "read", lambda data: dict(values))
    return values


def config(id: str) -> dict[str, Any]:
    return {"id": id, "label": "Research topic", "cadence": "weekly", "arxiv": {"categories": ["cs.CV"], "include_keywords": ["denoising"]}}


def file(data: Path, name: str) -> Path:
    return data / "topics" / name


def status_is(expected: int, call: Any, *args: Any) -> None:
    with pytest.raises(Exception) as e:
        call(*args)
    assert status(e.value) == expected, repr(e.value)


def test_crud_preserves_raw_fields_variables_and_neighboring_data(tmp_path: Path) -> None:
    data = tmp_path
    assert list_topics(data) == []
    status_is(404, get, data, "research")
    raw = config("research")
    raw["active"] = False
    raw["rss"] = {"feeds": [{"name": "Feed", "url": "${ISBE_TOPIC_TEST_HOST:-https://example.test}/feed"}], "exclude_keywords": ["广告"]}
    raw["retrieval"] = {"intent": "Read the original evidence", "key_entities": {"brands": ["Example"]}, "must_not_miss": ["benchmark"]}
    created = create(data, raw)
    assert created["config"] == raw
    assert len(created["revision"]) == 64
    assert "error" not in created
    assert "${ISBE_TOPIC_TEST_HOST:-https://example.test}" in file(data, "research.yaml").read_text()
    assert "window_days" not in created["config"]
    assert list_topics(data)[0]["active"] is False
    raw["label"] = "Updated label"
    updated = update(data, "research", raw, created["revision"])
    assert updated["config"] == raw
    assert created["revision"] != updated["revision"]
    (data / "reports/research").mkdir(parents=True)
    (data / "reports/research/latest.md").write_text("keep report")
    (data / "isbe.sqlite").write_text("keep database")
    (data / "memory.md").write_text("keep memory")
    delete(data, "research", updated["revision"])
    assert (data / "topics").is_dir()
    assert list_topics(data) == []
    for kept in ("reports/research/latest.md", "isbe.sqlite", "memory.md"):
        assert (data / kept).exists()


def test_existing_yml_is_updated_in_place_and_duplicate_extensions_conflict(data: Path) -> None:
    file(data, "t.yml").write_text(dump(config("t")))
    before = get(data, "t")
    changed = config("t")
    changed["label"] = "Changed"
    after = update(data, "t", changed, before["revision"])
    assert file(data, "t.yml").is_file()
    assert not file(data, "t.yaml").exists()
    status_is(409, create, data, config("t"))
    file(data, "t.yaml").write_text("duplicate")
    status_is(409, get, data, "t")
    status_is(409, create, data, config("t"))
    status_is(409, update, data, "t", config("t"), after["revision"])
    status_is(409, delete, data, "t", after["revision"])
    rows = list_topics(data)
    assert len(rows) == 1 and "error" in rows[0] and rows[0]["revision"] == ""
    file(data, "t.yaml").unlink()
    delete(data, "t", after["revision"])


def test_revisions_prevent_lost_edits_and_invalid_updates_preserve_bytes(data: Path) -> None:
    created = create(data, config("t"))
    original = file(data, "t.yaml").read_bytes()
    invalid = config("t")
    invalid["window_days"] = 0
    status_is(400, update, data, "t", invalid, created["revision"])
    status_is(400, update, data, "t", config("other"), created["revision"])
    status_is(409, delete, data, "t", "")
    assert file(data, "t.yaml").read_bytes() == original
    changed = original + b"\n# manual edit\n"
    file(data, "t.yaml").write_bytes(changed)
    status_is(409, update, data, "t", config("t"), created["revision"])
    status_is(409, delete, data, "t", created["revision"])
    assert file(data, "t.yaml").read_bytes() == changed
    assert not any(p.name.endswith(".tmp") for p in (data / "topics").iterdir())


def test_malformed_files_are_listed_and_can_be_repaired_or_deleted(data: Path) -> None:
    create(data, config("good"))
    file(data, "broken.yaml").write_text("id: [broken")
    file(data, "wrong-id.yml").write_text(dump(config("other")))
    rows = list_topics(data)
    assert [r["id"] for r in rows] == ["broken", "good", "wrong-id"]
    assert sum("error" in r for r in rows) == 2
    broken = get(data, "broken")
    assert broken["config"] is None
    assert broken["error"] == "topic file contains invalid YAML"
    assert len(broken["revision"]) == 64
    delete(data, "broken", broken["revision"])
    wrong = get(data, "wrong-id")
    assert wrong["config"]["id"] == "other"
    assert wrong["error"] == "id does not match file name"
    fixed = update(data, "wrong-id", config("wrong-id"), wrong["revision"])
    assert "error" not in fixed


def test_invalid_paths_file_types_and_oversized_documents_are_rejected(data: Path) -> None:
    for id in ["../outside", "a/b", "a\\b", ".", "con", "lpt1", "T", "a "]:
        status_is(400, create, data, config(id))
        status_is(400, get, data, id)
        status_is(400, delete, data, id, "revision")
    huge = config("huge")
    huge["label"] = "x" * MAX_BYTES
    status_is(400, create, data, huge)
    assert not file(data, "huge.yaml").exists()
    file(data, "directory.yaml").mkdir()
    status_is(400, get, data, "directory")
    status_is(400, create, data, config("directory"))
    file(data, "huge.yaml").write_bytes(b"x" * (MAX_BYTES + 1))
    bad = get(data, "huge")
    assert bad["config"] is None
    assert "64 KiB" in bad["error"]
    delete(data, "huge", bad["revision"])
    file(data, "latin.yaml").write_bytes(b"id: caf\xe9\n")
    assert get(data, "latin")["error"] == "topic file is not valid UTF-8"


def test_validation_reads_settings_saved_since_the_server_started(data: Path, saved: dict[str, str]) -> None:
    saved["ESSAIES_URL"] = "https://feeds.example.test"
    raw = {"id": "feed", "label": "Feed", "cadence": "daily", "rss": {"feeds": [{"name": "Saved setting", "url": "${ESSAIES_URL}/rss"}]}}
    created = create(data, raw)
    assert created["config"] == raw
    assert "error" not in created
    saved["ESSAIES_URL"] = "https://changed.example.test"
    assert "error" not in get(data, "feed")
    text = file(data, "feed.yaml").read_text()
    assert "${ESSAIES_URL}/rss" in text
    assert "feeds.example.test" not in text and "changed.example.test" not in text
    del saved["ESSAIES_URL"]
    assert get(data, "feed")["error"] == 'rss feed "Saved setting": undefined env var ESSAIES_URL'


def test_environment_overrides_saved_settings_for_validation(data: Path, saved: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    raw = {"id": "feed", "label": "Feed", "cadence": "daily", "rss": {"feeds": [{"name": "F", "url": "${ISBE_TOPIC_HOST}/rss"}]}}
    status_is(400, create, data, raw)
    monkeypatch.setenv("ISBE_TOPIC_HOST", "   ")
    status_is(400, create, data, raw)
    monkeypatch.setenv("ISBE_TOPIC_HOST", "https://env.test")
    assert "error" not in create(data, raw)


def test_unreadable_settings_are_an_internal_error(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    create(data, config("t"))

    def broken(data: Path) -> dict[str, str]:
        raise ValueError("settings.json is not a JSON object")

    monkeypatch.setattr(settings, "read", broken)
    assert get(data, "t")["error"] == "Topic settings could not be read. Review Settings and try again."
    status_is(500, create, data, config("other"))
    assert not file(data, "other.yaml").exists()


def test_concurrent_creates_never_replace_an_existing_topic(data: Path) -> None:
    outcomes: list[BaseException | None] = []
    barrier = threading.Barrier(4)

    def run() -> None:
        barrier.wait()
        try:
            create(data, config("race"))
            outcomes.append(None)
        except Exception as e:
            outcomes.append(e)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count(None) == 1
    assert sorted(status(e) for e in outcomes if e is not None) == [409, 409, 409]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_topic_links_and_linked_topics_directories_are_refused(tmp_path: Path) -> None:
    data, outside = tmp_path / "data", tmp_path / "outside"
    outside.mkdir()
    (outside / "t.yaml").write_text(dump(config("t")))
    (data / "topics").mkdir(parents=True)
    os.symlink(outside / "t.yaml", file(data, "t.yaml"))
    status_is(400, get, data, "t")
    status_is(400, create, data, config("t"))
    status_is(400, delete, data, "t", "revision")
    file(data, "t.yaml").unlink()
    (data / "topics").rmdir()
    os.symlink(outside, data / "topics")
    status_is(400, list_topics, data)
    status_is(400, get, data, "t")
    status_is(400, create, data, config("new"))
    assert not (outside / "new.yaml").exists()
    assert (outside / "t.yaml").exists()


def http_error(call: Any, *args: Any) -> tuple[int, str]:
    with pytest.raises(HttpError) as e:
        call(*args)
    return e.value.status, e.value.message


def test_errors_the_console_shows_are_http_errors(data: Path) -> None:
    assert http_error(create, data, {"label": "x"}) == (400, "topic id is required")
    assert http_error(create, data, "not an object") == (400, "topic id is required")
    assert http_error(update, data, "t", config("other"), "r") == (400, "topic id cannot be changed")
    assert http_error(update, data, "t", config("t"), "r") == (404, "topic not found")
    assert http_error(create, data, {**config("t"), "score": float("nan")}) == (400, "cannot serialize topic configuration")
    create(data, config("t"))
    assert http_error(create, data, config("t")) == (409, "topic already exists")
    assert http_error(update, data, "t", config("t"), "stale") == (409, "topic changed since it was loaded; reload before saving or deleting")
    assert http_error(get, data, "../t")[0] == 400


def test_yaml_1_1_words_survive_a_console_round_trip(data: Path) -> None:
    # A keyword list like [yes, no, on] must come back as the strings it was
    # written as, and stay strings after the console saves the document.
    file(data, "t.yaml").write_text(
        "id: t\nlabel: 2026-10-16\ncadence: weekly\nactive: no\n"
        "arxiv: {categories: [cs.CV], include_keywords: [yes, no, on, off]}\n"
    )
    detail = get(data, "t")
    assert "error" not in detail
    assert detail["config"]["label"] == "2026-10-16"
    assert detail["config"]["arxiv"]["include_keywords"] == ["yes", "no", "on", "off"]
    assert list_topics(data)[0]["active"] is False, "the list reads `active: no` like the Topic does"
    saved = update(data, "t", detail["config"], detail["revision"])
    assert saved["config"] == detail["config"]
    topic = parse(file(data, "t.yaml").read_text(), "t")
    assert topic.arxiv is not None and topic.arxiv.include_keywords == ["yes", "no", "on", "off"]
    assert topic.label == "2026-10-16" and topic.active is False
