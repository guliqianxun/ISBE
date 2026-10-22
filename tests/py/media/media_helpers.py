"""Shared by the media tests: the fixtures and a stand-in for `isbe.search.get`."""

import sqlite3
from pathlib import Path
from typing import Any

from isbe.core import db, jsonfmt
from isbe.core.errors import HttpError
from isbe.reports import media

TOPIC = "test-topic"
PLAN = {"id": "p", "name": "测试", "topic": "上海 ESG", "start_date": "2020-09-20", "end_date": "2020-10-19"}


def get_plan(conn: sqlite3.Connection, id: str) -> dict[str, Any]:
    """`search::get`, which media reads its plan through."""
    row = conn.execute(
        "SELECT p.config,p.revision,p.last_checked,p.last_status,"
        "(SELECT count(*) FROM search_materials WHERE plan_id=p.id) FROM search_plans p WHERE id=?1",
        (id,),
    ).fetchone()
    if row is None:
        raise HttpError(404, "Search plan not found")
    try:
        config = jsonfmt.loads(row[0])
    except ValueError:
        config = None
    return {"config": config, "revision": str(row[1]), "last_checked": row[2],
            "last_status": row[3], "item_count": row[4]}


def item(id: str, published: str | None, status: str) -> dict[str, Any]:
    return {"id": id, "url": f"https://news.example/{id}", "content": {
        "title": f"标题 {id}", "summary": "搜索返回的文字", "published_at": published, "date_status": status,
    }}


def connect(data: Path) -> sqlite3.Connection:
    """The database of the media tests: plan `p` with material `one`."""
    conn = db.connect(data / "isbe.db")
    conn.execute("INSERT INTO search_plans(id,config) VALUES('p',?1)", (jsonfmt.dumps(PLAN),))
    return conn


def add_material(conn: sqlite3.Connection, material: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO search_materials(id,plan_id,url,content,first_seen,last_seen) "
        "VALUES(?1,'p',?2,?3,'before','before')",
        (material["id"], material["url"], jsonfmt.dumps(material["content"])),
    )


def run(conn: sqlite3.Connection, data: Path, topic: str = TOPIC, week_end: str = "2020-10-18") -> dict[str, Any]:
    return media.run(conn, data, "p", topic, week_end, get_plan=get_plan)


def count(conn: sqlite3.Connection, table: str) -> int:
    value: int = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return value


def fingerprint(conn: sqlite3.Connection, period: str) -> str:
    value: str = conn.execute(
        "SELECT fingerprint FROM reports WHERE topic_id=?1 AND period=?2", (TOPIC, period)
    ).fetchone()[0]
    return value


def briefing_fixture(data: Path) -> tuple[sqlite3.Connection, dict[str, Any], dict[str, Any]]:
    """`briefing_fixture`: a media week with one cached original, and a draft citing it."""
    conn = connect(data)
    material = item("one", "2020-10-16", "in_range")
    material["content"]["publisher"] = "自定义媒体名称"
    add_material(conn, material)
    cached = {
        "title": "银行发布绿色融资安排的完整原文标题",
        "paragraphs": ["银行发布绿色融资安排。", "截至6月末，绿色贷款余额750亿元。"],
        "final_url": "https://news.example/one", "fetched_at": "2020-10-19T08:00:00Z",
    }
    conn.execute(
        "INSERT INTO article_text(url,content) VALUES('https://news.example/one',?1)",
        (jsonfmt.dumps(cached),),
    )
    parent = run(conn, data)
    draft = {
        "title": "上海 ESG 与绿色金融周报",
        "intro": "本期梳理银行已发布的绿色融资安排。",
        "sections": [{"title": "绿色融资", "items": [{
            "headline": "银行发布绿色融资安排", "body": ["银行发布绿色融资安排。"],
            "source_ids": ["one"],
            "evidence": [{"source_id": "one", "paragraph": 0, "quote": "银行发布绿色融资安排"}],
        }]}],
    }
    return conn, parent, draft
