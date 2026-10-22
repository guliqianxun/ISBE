"""Shared builders for the registry and search tests."""

import sqlite3

from isbe.search import Provider, SearchPlan
from isbe.sources.reader import Kind
from isbe.sources.registry import Source


def source(id: str) -> Source:
    return Source(
        id=id,
        name="Public news",
        topic="Shanghai",
        kind=Kind.RSS,
        url="https://example.com/feed",
        enabled=True,
        link_pattern="",
        max_items=100,
    )


def plan() -> SearchPlan:
    return SearchPlan(
        id="shanghai-esg",
        name="Shanghai ESG",
        topic="Shanghai",
        provider=Provider.SEARXNG,
        queries=["Shanghai ESG", "Shanghai green finance"],
        start_date="2026-10-01",
        end_date="2026-10-18",
        language="en",
        max_results=20,
    )


def count(conn: sqlite3.Connection, sql: str, *params: object) -> int:
    return int(conn.execute(sql, params).fetchone()[0])
