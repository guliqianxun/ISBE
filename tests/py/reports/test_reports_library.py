"""The report library: covers, the shelf, the reader view and the file lookup."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from isbe.core import db, jsonfmt
from isbe.reports import library
from isbe.reports.library import cover
from isbe.reports.library_cover import naive_date

TOPIC = "id: old-topic\nlabel: Current name\ncadence: weekly\narxiv:\n  categories: [cs.LG]\n  include_keywords: [learning]\n"


@pytest.fixture
def conn(data: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(data / "isbe.db")
    yield connection
    connection.close()


def add(
    conn: sqlite3.Connection,
    data: Path,
    topic: str,
    period: str,
    md: str | None,
    *,
    created: str = "2026-10-15",
    fingerprint: str = "{}",
    kind: str = "weekly",
    path: str | None = None,
) -> str:
    path = path or f"reports/{topic}/{period}.md"
    if md is not None:
        (data / path).parent.mkdir(parents=True, exist_ok=True)
        (data / path).write_bytes(md.encode())
    conn.execute(
        "INSERT INTO reports (id,topic_id,period,kind,path,fingerprint,created_at) VALUES (?,?,?,?,?,?,?)",
        (f"{topic}/{period}/{kind}", topic, period, kind, path, fingerprint, created),
    )
    return path


def test_cover_reads_label_window_tldr_and_papers() -> None:
    c = cover(
        "# 临近降水预报科研订阅 — 2026-W42\n\n> 覆盖窗口：2026-10-07 → 2026-10-14（论文按 arXiv 更新日期计）\n\n"
        "## TL;DR\n\n- 本期 4 篇\n- **扩散**继续主导\n\n## 论文逐篇\n\n### [2610.1] A\n- x\n### [2610.2] B\n"
    )
    assert c == {"label": "临近降水预报科研订阅 — 2026-W42", "window": "2026-10-07 → 2026-10-14",
                 "tldr": ["本期 4 篇", "扩散继续主导"], "papers": 2}
    briefing = cover("# 行业周报\n> 2026-10-12 — 2026-10-18\n## 本周速览\n- [银行发布绿色融资安排](https://example.org/news)\n")
    assert briefing["window"] == "2026-10-12 → 2026-10-18"
    assert briefing["tldr"] == ["银行发布绿色融资安排"]
    assert cover("> no dates → here")["window"] is None
    assert cover("## TL;DR\n- 结果 [E1](/api/research/evidence?topic=t&id=E1)\n")["tldr"] == ["结果"]
    assert cover(
        "## 核心判断\n- 结果 [机构｜长标题](/api/research/evidence?topic=t&id=E1) "
        "[补充摘录](/api/research/evidence?topic=t&id=E2)\n"
    )["tldr"] == ["结果"]
    assert cover("") == {"label": None, "window": None, "tldr": [], "papers": 0}


def test_cover_follows_the_string_and_date_rules() -> None:
    # Only the first H1 and the first parsable window count; three bullets at most.
    md = "#  First  \r\n# Second\r\n> 2026-02-30 → 2026-03-01\r\n> 自  2026-1-7 → 2026-01-09 起\r\n" \
         "## TL;DR \r\n- a `code` &amp; <b>b</b>\r\n- ![alt *x*](/i.png)\r\n- c\r\n- d\r\n  ### [x] indented\r\n"
    c = cover(md)
    assert c["label"] == "First"
    assert c["window"] == "  2026-1-7 → 2026-01-09", "the 10 bytes before the arrow, kept as written"
    assert c["tldr"] == ["a code & b", "alt x", "c"]
    assert c["papers"] == 1
    assert cover("> 日期2026-10-07→2026-10-14")["window"] == "2026-10-07 → 2026-10-14"
    # Ten bytes that start inside a multi-byte character are no window.
    assert cover("> 期26-10-07 → 2026-10-14")["window"] is None
    assert cover("> 2026-10-07 → 2026-10-1")["window"] is None
    assert cover("## Other\n- not a summary\n## TL;DR\n- yes\n")["tldr"] == ["yes"]
    assert cover("## TL;DR\n-no space\n* star\n")["tldr"] == []
    for text, ok in [("2026-10-07", True), (" 2026-1-07", True), ("2026 - 1-7", True), ("+2026-1-07", True),
                     ("2024-02-29", True), ("2023-02-29", False), ("2026-13-01", False), ("2026-10-00", False),
                     ("2026/10/07", False), ("２026-10-07", False), ("2026-10-07x", False), ("", False)]:
        assert naive_date(text) is ok, text


def test_deleted_topics_use_historical_titles_consistently_without_rewriting_reports(
    conn: sqlite3.Connection, data: Path
) -> None:
    topic_file = data / "topics" / "old-topic.yaml"
    topic_file.write_text(TOPIC)
    for period, title, show_period in [
        ("2026-W41", "旧项目观察 · 2026-W41", False),
        ("2026-W40", "旧项目观察 — 2026-W40", False),
        ("2026-W39", "Old research", True),
    ]:
        add(conn, data, "old-topic", period, f"# {title}\n\nOriginal report body.\n")
        reader = library.report(conn, data, "old-topic", period)
        assert reader is not None and reader["label"] == "Current name"
        assert show_period == (period not in title)
    topic_file.unlink()
    shelf = library.library(conn, data)
    assert len(shelf) == 3
    for c in shelf:
        period = c["period"]
        reader = library.report(conn, data, "old-topic", period)
        found = library.report_file(conn, data, "old-topic", period)
        assert reader is not None and found is not None
        md = found[1]
        title = md.split("\n", 1)[0].removeprefix("# ")
        assert c["label"] == title == reader["label"]
        assert reader["show_period"] == c["show_period"] == (period == "2026-W39")
        assert md == f"# {title}\n\nOriginal report body.\n"
        assert title in reader["html"]


def test_missing_report_file_is_not_found(conn: sqlite3.Connection, data: Path) -> None:
    add(conn, data, "t", "2026-W41", None)
    # The default "latest report" view (no topic, no period) and the exact one.
    for topic, period in [("", ""), ("t", ""), ("t", "2026-W41")]:
        assert library.report_file(conn, data, topic, period) is None
        assert library.report(conn, data, topic, period) is None
        with pytest.raises(library.ReportMissing):
            library.find_report(conn, data, topic, period)
    # Also without a reports directory at all.
    (data / "reports").rmdir()
    assert library.report(conn, data, "", "") is None
    # The shelf still lists the row, with an empty cover.
    [c] = library.library(conn, data)
    assert c["label"] == "t" and c["papers"] == 0 and c["tldr"] == []


def test_report_file_falls_back_to_newest_and_stays_inside_reports(conn: sqlite3.Connection, data: Path) -> None:
    add(conn, data, "a", "2026-W40", "# a40\r\n", created="2026-10-01")
    add(conn, data, "a", "2026-W41", "# a41\n", created="2026-10-08")
    add(conn, data, "b", "2026-W39", "# b39\n", created="2026-10-09")
    assert library.report_file(conn, data, "a", "2026-W40") == (
        {"topic_id": "a", "period": "2026-W40", "kind": "weekly", "path": "reports/a/2026-W40.md",
         "created_at": "2026-10-01"},
        "# a40\r\n",
    ), "newlines are kept exactly"
    assert library.report_file(conn, data, "a", "")[1] == "# a41\n"  # type: ignore[index]
    assert library.report_file(conn, data, "", "")[1] == "# b39\n"  # type: ignore[index]
    assert library.report_file(conn, data, "", "2026-W40")[1] == "# a40\r\n"  # type: ignore[index]
    assert library.report_file(conn, data, "missing", "") is None
    (data / "secret.md").write_text("secret")
    for n, path in enumerate(["../secret.md", "reports/../secret.md", "secret.md", "reports\\x.md", "reports/c:x.md"]):
        add(conn, data, f"u{n}", "p", None, path=path)
        assert library.report_file(conn, data, f"u{n}", "p") is None, path
    (data / "reports" / "link").mkdir()
    (data / "reports" / "link" / "out.md").symlink_to(data / "secret.md")
    add(conn, data, "link", "out", None, path="reports/link/out.md")
    assert library.report_file(conn, data, "link", "out") is None
    (data / "reports" / "link" / "in.md").symlink_to(data / "reports" / "a" / "2026-W41.md")
    add(conn, data, "link", "in", None, path="reports/link/in.md")
    assert library.report_file(conn, data, "link", "in")[1] == "# a41\n"  # type: ignore[index]
    assert library.download_name({"topic_id": "a", "period": "2026-W40"}) == "a-2026-W40.md"
    assert library.download_name({"topic_id": None, "period": None}) == "report-.md"
    assert library.download_name({"topic_id": 'x"y 中', "period": "p"}) == "xy-p.md"


def test_shelf_reader_and_overview_counts(conn: sqlite3.Connection, data: Path) -> None:
    (data / "topics" / "old-topic.yaml").write_text(TOPIC)
    (data / "topics" / "broken.yaml").write_text("id: [")
    media = jsonfmt.dumps({"mode": "media-weekly", "materials": [{"id": 1}, {"id": 2}]})
    add(conn, data, "old-topic", "2026-W40", "# T\n\n### [1] a\n  ### [2] b\n", created="2026-10-01")
    add(conn, data, "old-topic", "2026-W41", "# 行业周报\n", created="2026-10-08", fingerprint=media, kind="research")
    add(conn, data, "imp", "2026-W41", "# Imported\n", created="2026-10-02",
        fingerprint='{"imported_from":"x.md"}')
    add(conn, data, "odd", "p", "", created="2026-09-01", fingerprint="not json")
    shelf = {(c["topic_id"], c["period"]): c for c in library.library(conn, data)}
    assert list(shelf) == [("imp", "2026-W41"), ("odd", "p"), ("old-topic", "2026-W41"), ("old-topic", "2026-W40")]
    first = shelf[("old-topic", "2026-W40")]
    assert first == {"label": "Current name", "window": None, "tldr": [], "papers": 2, "show_period": True,
                     "topic_id": "old-topic", "period": "2026-W40", "kind": "weekly", "materials": 0,
                     "media": False, "created_at": "2026-10-01", "imported": False}, "a broken topic file keeps the other labels"
    assert shelf[("old-topic", "2026-W41")]["media"] is True and shelf[("old-topic", "2026-W41")]["materials"] == 2
    assert shelf[("imp", "2026-W41")]["imported"] is True and shelf[("imp", "2026-W41")]["label"] == "Imported"
    assert shelf[("odd", "p")]["materials"] is None and shelf[("odd", "p")]["label"] == "odd"

    calls: list[tuple[str, str]] = []

    def articles(c: sqlite3.Connection, d: Path, topic: str, period: str) -> list[dict[str, Any]]:
        calls.append((topic, period))
        return [{"id": "m1", "url": "https://example.org/a"}]

    reader = library.report(conn, data, "old-topic", "", articles=articles)
    assert reader is not None
    assert calls == [("old-topic", "2026-W41")]
    assert reader["articles"] == [{"id": "m1", "url": "https://example.org/a"}]
    assert {k: reader[k] for k in ("topic", "label", "period", "kind", "path", "created_at", "show_period")} == {
        "topic": "old-topic", "label": "Current name", "period": "2026-W41", "kind": "research",
        "path": "reports/old-topic/2026-W41.md", "created_at": "2026-10-08", "show_period": True}
    assert reader["html"] == "<h1>行业周报</h1>\n"
    assert reader["issues"] == [
        {"period": "2026-W41", "kind": "research", "media": True, "materials": 2, "papers": 0},
        {"period": "2026-W40", "kind": "weekly", "media": False, "materials": 0, "papers": 2},
    ]
    assert reader["topics"] == [{"topic_id": "imp"}, {"topic_id": "odd"}, {"topic_id": "old-topic"}]

    def failing(*_: Any) -> list[dict[str, Any]]:
        raise RuntimeError("bundle unreadable")

    assert library.report(conn, data, "old-topic", "2026-W41", articles=failing)["articles"] == []  # type: ignore[index]
    assert library.report(conn, data, "imp", "")["articles"] == []  # type: ignore[index]

    recent = library.recent(conn, data)
    assert [r["period"] for r in recent] == ["2026-W41", "2026-W41", "2026-W40", "p"]
    assert recent[0] == {"topic_id": "old-topic", "period": "2026-W41", "kind": "research", "created_at": "2026-10-08",
                         "path": "reports/old-topic/2026-W41.md", "papers": 0, "media": True, "materials": 2, "label": "行业周报"}
    assert recent[-1]["materials"] == 0, "the overview counts a broken fingerprint as no materials"
    assert len(library.recent(conn, data, 2)) == 2
    assert library.latest(conn, data, "old-topic") == {"period": "2026-W41", "kind": "research",
                                                        "created_at": "2026-10-08", "papers": 0}
    assert library.latest(conn, data, "none") is None
    assert library.papers_in(data, "reports/old-topic/2026-W40.md") == 2
    assert library.papers_in(data, "reports/../isbe.db") == 0
