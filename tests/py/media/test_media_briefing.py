"""Media briefings: freezing cached originals, source checks, snapshot contents and the frozen layout."""

import copy
from pathlib import Path

import pytest
from media_helpers import TOPIC, briefing_fixture, count, fingerprint, get_plan

from isbe.core import db, jsonfmt
from isbe.reports import media
from isbe.reports.media_files import MediaError, snapshot


def test_briefing_freezes_cached_original_and_preserves_parent_without_model_calls(
    media_data: Path,
) -> None:
    data = media_data
    conn, parent, draft = briefing_fixture(data)
    parent_period = parent["period"]
    parent_path = data / parent["path"]
    parent_snapshot = data / f"reports/test-topic/{parent_period}.sources.json"
    parent_body = parent_path.read_bytes()
    parent_manifest = parent_snapshot.read_bytes()
    parent_fingerprint = fingerprint(conn, parent_period)
    input = data / "briefing-input.json"
    input.write_text(jsonfmt.dumps(draft), encoding="utf-8")
    created = media.create_briefing(conn, data, TOPIC, parent_period, input)
    period = created["period"]
    assert period == "2020-W42-briefing"
    report_path = data / created["path"]
    report = report_path.read_text(encoding="utf-8")
    assert "## 绿色融资" in report
    assert "银行发布绿色融资安排的完整原文标题" in report
    assert "检索摘要" not in report
    frozen = snapshot(conn, data, TOPIC, period)
    assert frozen is not None
    assert frozen["parent_period"] == parent_period
    assert frozen["presentation"] == "industry-briefing"
    assert frozen["editorial"] == draft
    assert media.articles(conn, data, TOPIC, period) == [{"id": "one", "url": "https://news.example/one"}]
    saved_original = media.original(conn, data, TOPIC, period, "one")
    assert saved_original is not None
    assert saved_original["paragraphs"][0] == "银行发布绿色融资安排。"
    assert saved_original["fetched_at"] == "2020-10-19T08:00:00Z"
    conn.execute(
        "UPDATE article_text SET content=json_set(content,'$.paragraphs',json('[\"后来更新的正文\"]'),"
        "'$.title','更新标题')"
    )
    assert media.original(conn, data, TOPIC, period, "one") == saved_original
    assert report_path.read_text(encoding="utf-8") == report
    assert parent_path.read_bytes() == parent_body
    assert parent_snapshot.read_bytes() == parent_manifest
    assert fingerprint(conn, parent_period) == parent_fingerprint
    assert media.original(conn, data, TOPIC, parent_period, "one") is None
    assert media.original(conn, data, TOPIC, period, "missing") is None
    assert count(conn, "reports") == 2
    assert count(conn, "llm_calls") == 0


def test_briefing_rejects_unverifiable_sources_and_rolls_back_failed_persistence(
    media_data: Path,
) -> None:
    data = media_data
    conn, parent, draft = briefing_fixture(data)
    parent_period = parent["period"]
    report_dir = data / "reports/test-topic"
    input = data / "briefing-input.json"
    before = len(list(report_dir.iterdir()))
    unknown = copy.deepcopy(draft)
    unknown["sections"][0]["items"][0]["source_ids"] = ["unknown"]
    unknown["sections"][0]["items"][0]["evidence"][0]["source_id"] = "unknown"
    mismatched = copy.deepcopy(draft)
    mismatched["sections"][0]["items"][0]["evidence"][0]["quote"] = "原文没有这句话"
    wrong_paragraph = copy.deepcopy(draft)
    wrong_paragraph["sections"][0]["items"][0]["evidence"][0]["paragraph"] = 1
    for invalid in [unknown, mismatched, wrong_paragraph]:
        input.write_text(jsonfmt.dumps(invalid), encoding="utf-8")
        with pytest.raises(ValueError):
            media.create_briefing(conn, data, TOPIC, parent_period, input)
        assert len(list(report_dir.iterdir())) == before
        assert count(conn, "reports") == 1
    # A database failure after both files were written must remove only those new files.
    conn.execute(
        "CREATE TRIGGER reject_briefing BEFORE INSERT ON reports "
        "BEGIN SELECT RAISE(FAIL,'test database failure'); END;"
    )
    input.write_text(jsonfmt.dumps(draft), encoding="utf-8")
    with pytest.raises(Exception, match="test database failure"):
        media.create_briefing(conn, data, TOPIC, parent_period, input)
    assert len(list(report_dir.iterdir())) == before
    assert not (report_dir / "2020-W42-briefing.md").exists()
    assert not (report_dir / "2020-W42-briefing.sources.json").exists()
    assert count(conn, "reports") == 1
    assert count(conn, "llm_calls") == 0
    assert snapshot(conn, data, TOPIC, parent_period) is not None
    assert not conn.in_transaction


def test_briefing_snapshot_keeps_only_cited_materials_with_their_publisher(media_data: Path) -> None:
    data = media_data
    conn, parent, draft = briefing_fixture(data)
    conn.execute(
        "INSERT INTO search_materials(id,plan_id,url,content,first_seen,last_seen) VALUES"
        "('two','p','https://www.thepaper.cn/two',?1,'b','b')",
        (jsonfmt.dumps({"title": "第二篇", "publisher": "www.thepaper.cn", "date_status": "unknown"}),),
    )
    parent = media.run(conn, data, "p", TOPIC, "2020-10-18", get_plan=get_plan)
    assert parent["period"] == "2020-W42-media-v2"
    draft["schedule"] = [{"when": "10月", "arrangement": "安排", "source_id": "two", "paragraph": 0,
                          "quote": "第二篇原文"}]
    conn.execute(
        "INSERT INTO article_text(url,content) VALUES('https://www.thepaper.cn/two',?1)",
        (jsonfmt.dumps({"paragraphs": ["第二篇原文。"], "final_url": "https://www.thepaper.cn/two",
                        "fetched_at": "2020-10-19T08:00:00Z"}),),
    )
    input = data / "briefing-input.json"
    input.write_text(jsonfmt.dumps(draft), encoding="utf-8")
    created = media.create_briefing(conn, data, TOPIC, parent["period"], input)
    assert created == {
        "topic": TOPIC, "period": "2020-W42-briefing", "path": "reports/test-topic/2020-W42-briefing.md",
        "url": "/reports?topic=test-topic&period=2020-W42-briefing",
    }
    text = (data / "reports/test-topic/2020-W42-briefing.sources.json").read_text(encoding="utf-8")
    frozen = jsonfmt.loads(text)
    assert [m["id"] for m in frozen["materials"]] == ["one", "two"]
    one, two = frozen["materials"]
    # An editor-set publisher stays; a host-like one becomes the publisher's name.
    assert one["content"]["publisher"] == "自定义媒体名称"
    assert two["content"]["publisher"] == "澎湃新闻"
    assert two["content"]["title"] == "第二篇"
    assert one["content"]["original"] == {
        "cached": True, "title": "银行发布绿色融资安排的完整原文标题",
        "paragraphs": ["银行发布绿色融资安排。", "截至6月末，绿色贷款余额750亿元。"],
        "final_url": "https://news.example/one", "fetched_at": "2020-10-19T08:00:00Z",
    }
    assert frozen["period"] == "2020-W42-briefing"
    assert frozen["parent_period"] == "2020-W42-media-v2"
    assert frozen["plan"]["config"]["id"] == "p"
    assert jsonfmt.loads(fingerprint(conn, "2020-W42-briefing")) == {
        "mode": "media-weekly", "presentation": "industry-briefing", "materials": ["one", "two"],
        "parent_period": "2020-W42-media-v2",
        "snapshot_path": "reports/test-topic/2020-W42-briefing.sources.json",
        "snapshot_sha256": db.sha256(text),
    }
    again = media.create_briefing(conn, data, TOPIC, parent["period"], input)
    assert again["period"] == "2020-W42-briefing-v2"


def test_briefing_input_and_parent_errors(media_data: Path) -> None:
    data = media_data
    conn, parent, draft = briefing_fixture(data)
    input = data / "briefing-input.json"
    input.write_text(jsonfmt.dumps(draft), encoding="utf-8")
    with pytest.raises(MediaError, match="Media report not found"):
        media.create_briefing(conn, data, TOPIC, "2020-W41-media", input)
    with pytest.raises(MediaError, match=r"No such file or directory \(os error 2\)"):
        media.create_briefing(conn, data, TOPIC, parent["period"], data / "missing.json")
    input.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(MediaError, match="Briefing input is too large"):
        media.create_briefing(conn, data, TOPIC, parent["period"], input)
    input.write_bytes(b"\xff")
    with pytest.raises(MediaError, match="stream did not contain valid UTF-8"):
        media.create_briefing(conn, data, TOPIC, parent["period"], input)
    assert count(conn, "reports") == 1
    assert not conn.in_transaction


def test_non_object_content_fails_instead_of_panicking(media_data: Path) -> None:
    data = media_data
    conn, parent, draft = briefing_fixture(data)
    path = data / f"reports/test-topic/{parent['period']}.sources.json"
    frozen = jsonfmt.loads(path.read_text(encoding="utf-8"))
    frozen["materials"][0]["content"] = "text"
    text = jsonfmt.dumps_pretty(frozen)
    path.write_text(text, encoding="utf-8")
    conn.execute(
        "UPDATE reports SET fingerprint=json_set(fingerprint,'$.snapshot_sha256',?1)", (db.sha256(text),)
    )
    input = data / "briefing-input.json"
    input.write_text(jsonfmt.dumps(draft), encoding="utf-8")
    with pytest.raises(MediaError, match='cannot access key "title" in JSON string'):
        media.create_briefing(conn, data, TOPIC, parent["period"], input)
    assert count(conn, "reports") == 1
