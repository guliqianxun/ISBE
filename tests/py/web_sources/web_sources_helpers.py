"""Requests into the console with the source routes mounted."""

import json
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from isbe import search
from isbe.reports import media
from isbe.search import Provider, SearchPlan
from isbe.sources import registry
from isbe.web import create_app
from isbe.web.app import Endpoint
from isbe.web.console import Console
from isbe.web.routes_sources import api
from isbe.web.wire import BodyState, Request

TOKEN = "source-csrf"


def console(root: Path, **kwargs: Any) -> Console:
    kwargs.setdefault("local_port", 8080)
    app = create_app(root, token=TOKEN, sources=api, articles=media.articles, **kwargs)
    made: Console = app.state.console
    return made


def config(id: str) -> dict[str, Any]:
    return {"id": id, "name": "City news", "topic": "上海 ESG", "kind": "rss",
            "url": "https://example.org/feed", "enabled": False}


def send(
    target: Console,
    method: str,
    path: str,
    body: Any = None,
    *,
    host: str = "127.0.0.1:8080",
    token: str = TOKEN,
    content_type: str = "application/json",
    raw: bytes | None = None,
) -> tuple[int, Any, list[tuple[str, str]]]:
    """One request through the dispatcher, as the ASGI layer hands it over."""
    path, _, query = path.partition("?")
    headers = [("host", host), ("x-isbe-csrf", token), ("content-type", content_type)]
    req = Request(method, path, query, headers, "127.0.0.1")
    if method in ("POST", "PUT", "DELETE"):
        req.body = raw if raw is not None else json.dumps(body).encode()
        if len(req.body) > target.limits.body_bytes:
            req.body = BodyState.TOO_LARGE
    reply = Endpoint(target)._handle(req)
    return reply.status, json.loads(reply.body), reply.headers


def request(target: Console, method: str, path: str, body: Any = None) -> tuple[int, Any]:
    status, value, _ = send(target, method, path, body)
    return status, value


def count(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def wait_for(conn: sqlite3.Connection, root: Path, job: str, done: Callable[[], bool] | None = None) -> dict[str, Any]:
    """The job once it has finished and released the execution lock."""
    deadline = time.monotonic() + 5
    while True:
        found = registry.job(conn, job)
        if found["state"] != "running" and registry.recover(root) and (done is None or done()):
            return found
        assert time.monotonic() < deadline, f"job {job} did not finish"
        time.sleep(0.01)


def weekly_fixture(conn: sqlite3.Connection) -> None:
    plan = SearchPlan(id="plan", name="ESG news", topic="上海 ESG", provider=Provider.SEARXNG, queries=["上海 ESG"],
                      start_date="2020-09-20", end_date="2020-10-19", language="zh-CN", max_results=10)
    search.save(conn, plan, None, None)
    content = ('{"date_status":"in_range","published_at":"2020-10-16","summary":"来源给出的摘要",'
               '"title":"机构发布 ESG 报告"}')
    conn.execute(
        "INSERT INTO search_materials(id,plan_id,url,content,first_seen,last_seen) VALUES "
        "('one','plan','https://example.org/article',?1,'2020-10-19','2020-10-19')",
        (content,),
    )
