"""The media week: new versions, frozen materials, skipped periods and the frozen layout."""

import os
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from media_helpers import (
    TOPIC,
    add_material,
    briefing_fixture,
    connect,
    count,
    fingerprint,
    get_plan,
    item,
    run,
)

from isbe.core import db, jsonfmt
from isbe.core.errors import HttpError
from isbe.reports import library, media
from isbe.reports.media_files import MediaError


def test_writes_new_versions_and_freezes_prior_materials_without_model_calls(media_data: Path) -> None:
    data = media_data
    conn = connect(data)
    add_material(conn, item("one", "2020-10-16", "in_range"))
    first = run(conn, data)
    body = (data / first["path"]).read_bytes()
    manifest = data / "reports/test-topic/2020-W42-media.sources.json"
    snapshot = manifest.read_bytes()
    assert media.articles(conn, data, TOPIC, "2020-W42-media") == [
        {"id": "one", "url": "https://news.example/one"}
    ]
    assert media.articles(conn, data, TOPIC, "missing") == []
    with pytest.raises(HttpError) as invalid:
        media.articles(conn, data, "../escape", "2020-W42-media")
    assert (invalid.value.status, invalid.value.message) == (400, "Invalid report identifier")
    conn.execute(
        "UPDATE search_materials SET url='https://news.example/changed',"
        "content=json_set(content,'$.summary','changed','$.published_at','2020-09-01')"
    )
    second = run(conn, data)
    assert first["period"] == "2020-W42-media"
    assert second["period"] == "2020-W42-media-v2"
    assert first["weekly"] == 1
    assert second["weekly"] == 0
    assert (data / first["path"]).read_bytes() == body
    assert manifest.read_bytes() == snapshot
    assert media.articles(conn, data, TOPIC, "2020-W42-media")[0]["url"] == "https://news.example/one"
    assert count(conn, "llm_calls") == 0
    with pytest.raises(HttpError):
        run(conn, data, topic="../escape")
    with pytest.raises(HttpError):
        run(conn, data, week_end="2020-10-19")
    conn.execute(
        "CREATE TRIGGER reject_media BEFORE INSERT ON reports "
        "BEGIN SELECT RAISE(FAIL,'test database failure'); END;"
    )
    with pytest.raises(Exception, match="test database failure"):
        run(conn, data)
    assert not (data / "reports/test-topic/2020-W42-media-v3.md").exists()
    assert not (data / "reports/test-topic/2020-W42-media-v3.sources.json").exists()
    assert count(conn, "reports") == 2
    saved = fingerprint(conn, "2020-W42-media")
    manifest.write_bytes(b"{}")
    with pytest.raises(MediaError, match="Report snapshot has changed"):
        media.articles(conn, data, TOPIC, "2020-W42-media")
    manifest.write_bytes(snapshot)
    conn.execute(
        "UPDATE reports SET fingerprint=json_set(fingerprint,'$.snapshot_path','../outside.json') "
        "WHERE period='2020-W42-media'"
    )
    with pytest.raises(MediaError, match="Invalid report snapshot path"):
        media.articles(conn, data, TOPIC, "2020-W42-media")
    conn.execute("UPDATE reports SET fingerprint=?1 WHERE period='2020-W42-media'", (saved,))
    mismatched = jsonfmt.loads(snapshot)
    mismatched["topic"] = "other-topic"
    text = jsonfmt.dumps(mismatched)
    manifest.write_text(text, encoding="utf-8")
    conn.execute(
        "UPDATE reports SET fingerprint=json_set(fingerprint,'$.snapshot_sha256',?1) "
        "WHERE period='2020-W42-media'",
        (db.sha256(text),),
    )
    with pytest.raises(MediaError, match="does not match this report"):
        media.articles(conn, data, TOPIC, "2020-W42-media")
    manifest.unlink()
    with pytest.raises(MediaError, match=r"No such file or directory \(os error 2\)"):
        media.articles(conn, data, TOPIC, "2020-W42-media")
    conn.execute("UPDATE reports SET fingerprint='{}' WHERE period='2020-W42-media'")
    assert media.articles(conn, data, TOPIC, "2020-W42-media") == []


def test_snapshot_fingerprint_row_and_stats_shape(media_data: Path) -> None:
    data = media_data
    conn = connect(data)
    add_material(conn, item("one", "2020-10-16", "in_range"))
    add_material(conn, item("two", None, "unknown"))
    stats = run(conn, data)
    assert stats == {
        "weekly": 1, "background": 0, "newer": 0, "unknown": 1, "conflict": 0, "outside": 0,
        "topic": TOPIC, "period": "2020-W42-media", "kind": "weekly",
        "path": "reports/test-topic/2020-W42-media.md", "materials": 2,
        "url": "/reports?topic=test-topic&period=2020-W42-media",
    }
    text = (data / "reports/test-topic/2020-W42-media.sources.json").read_text(encoding="utf-8")
    frozen = jsonfmt.loads(text)
    assert text == jsonfmt.dumps_pretty(frozen)
    assert frozen["plan"] == get_plan(conn, "p")
    assert frozen["plan"]["revision"] == "1" and frozen["plan"]["item_count"] == 2
    assert {k: v for k, v in frozen.items() if k not in ("plan", "materials", "created_at")} == {
        "mode": "media-weekly", "topic": TOPIC, "period": "2020-W42-media",
        "week_start": "2020-10-12", "week_end": "2020-10-18", "timezone": "Asia/Shanghai",
    }
    assert frozen["materials"][0] == {
        "id": "one", "url": "https://news.example/one", "first_seen": "before", "last_seen": "before",
        "content": item("one", "2020-10-16", "in_range")["content"],
    }
    row = conn.execute("SELECT id,topic_id,period,kind,path,fingerprint,created_at FROM reports").fetchone()
    assert tuple(row)[:5] == (
        "test-topic/2020-W42-media/weekly", TOPIC, "2020-W42-media", "weekly",
        "reports/test-topic/2020-W42-media.md",
    )
    assert row[6] == frozen["created_at"]
    assert row[5] == jsonfmt.dumps({
        "mode": "media-weekly", "materials": ["one", "two"],
        "counts": {k: stats[k] for k in ("weekly", "background", "newer", "unknown", "conflict", "outside")},
        "plan_id": "p", "week_start": "2020-10-12", "week_end": "2020-10-18",
        "snapshot_path": "reports/test-topic/2020-W42-media.sources.json",
        "snapshot_sha256": db.sha256(text),
    })


def coming_sunday() -> str:
    """This week's Sunday in Shanghai (today, if it is Sunday): not yet a completed week."""
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    return (today + timedelta(days=6 - today.weekday())).isoformat()


def test_invalid_requests_answer_with_the_established_errors(media_data: Path) -> None:
    data = media_data
    conn = connect(data)
    for kwargs, message in [
        ({"topic": "../escape"}, "Invalid report topic identifier"),
        ({"week_end": "2020-10-19"}, "The report week must end on Sunday"),
        ({"week_end": "2020-10-1"}, "Invalid week ending date"),
        ({"week_end": coming_sunday()}, "Choose a completed week"),
        ({}, "The search plan has no collected materials"),
    ]:
        with pytest.raises(HttpError) as error:
            run(conn, data, **kwargs)
        assert (error.value.status, error.value.message) == (400, message)
    with pytest.raises(HttpError) as missing:
        media.run(conn, data, "missing", TOPIC, "2020-10-18", get_plan=get_plan)
    assert missing.value.status == 404
    conn.execute("UPDATE search_plans SET config=json_set(config,'$.start_date','2020-10-20')")
    add_material(conn, item("one", "2020-10-16", "in_range"))
    with pytest.raises(MediaError, match="Invalid saved search window"):
        run(conn, data)
    conn.execute("UPDATE search_plans SET config=json_remove(config,'$.end_date')")
    with pytest.raises(MediaError, match="Missing search end date"):
        run(conn, data)
    assert count(conn, "reports") == 0
    assert not conn.in_transaction


def test_skips_periods_held_by_any_report_or_file(media_data: Path) -> None:
    data = media_data
    conn = connect(data)
    add_material(conn, item("one", "2020-10-16", "in_range"))
    conn.execute(
        "INSERT INTO reports(id,topic_id,period,kind,path,fingerprint,created_at) "
        "VALUES('x','test-topic','2020-W42-media','research','reports/test-topic/2020-W42-media.md','{}','t')"
    )
    folder = data / "reports" / TOPIC
    folder.mkdir(parents=True)
    os.symlink(data / "nowhere", folder / "2020-W42-media-v2.sources.json")
    (folder / "2020-W42-media-v3.md").write_text("someone else's", encoding="utf-8")
    assert run(conn, data)["period"] == "2020-W42-media-v4"
    assert (folder / "2020-W42-media-v3.md").read_text(encoding="utf-8") == "someone else's"


def test_refuses_linked_report_directories(media_data: Path, tmp_path: Path) -> None:
    data = media_data
    conn = connect(data)
    add_material(conn, item("one", "2020-10-16", "in_range"))
    (tmp_path / "elsewhere").mkdir()
    (data / "reports").mkdir()
    os.symlink(tmp_path / "elsewhere", data / "reports" / TOPIC)
    with pytest.raises(MediaError, match="Report paths must not contain links"):
        run(conn, data)
    assert list((tmp_path / "elsewhere").iterdir()) == []


def test_default_plan_reader_is_isbe_search_get(media_data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = media_data
    conn = connect(data)
    add_material(conn, item("one", "2020-10-16", "in_range"))
    fake = types.ModuleType("isbe.search")
    fake.get = get_plan  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "isbe.search", fake)
    assert media.run(conn, data, "p", TOPIC, "2020-10-18")["period"] == "2020-W42-media"


def test_library_reader_lists_the_frozen_articles(media_data: Path) -> None:
    data = media_data
    conn = connect(data)
    add_material(conn, item("one", "2020-10-16", "in_range"))
    run(conn, data)
    articles: library.Articles = media.articles
    view = library.report(conn, data, TOPIC, "2020-W42-media", articles=articles)
    assert view is not None
    assert view["articles"] == [{"id": "one", "url": "https://news.example/one"}]
    assert view["issues"][0]["media"] is True and view["issues"][0]["materials"] == 1


def test_briefing_refuses_a_dangling_link_at_its_name(media_data: Path) -> None:
    data = media_data
    conn, parent, draft = briefing_fixture(data)
    os.symlink(data / "nowhere", data / "reports" / TOPIC / "2020-W42-briefing.md")
    input = data / "briefing-input.json"
    input.write_text(jsonfmt.dumps(draft), encoding="utf-8")
    with pytest.raises(MediaError, match="Report paths must not contain links"):
        media.create_briefing(conn, data, TOPIC, parent["period"], input)
    assert count(conn, "reports") == 1
    assert not (data / "reports" / TOPIC / "2020-W42-briefing.sources.json").exists()
