"""Saved search plans.

A plan is stored as compact JSON with its fields in declaration order. Every
other JSON value these modules store (materials, hits, stats) has sorted keys,
so existing rows and hashes stay comparable.
"""

import re
import sqlite3
from datetime import date
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from isbe.core import db, jsonfmt
from isbe.core.errors import HttpError
from isbe.core.validators import is_control, trim
from isbe.topics.model import TopicError, check_id

from . import query as query_syntax

_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class Provider(StrEnum):
    # Removed on 2026-10-21. The value stays so that plans saved for it, their past
    # runs and the date rechecks of their material still load; `validated` refuses it,
    # so such a plan can be listed and rechecked but not saved or run until switched.
    BAIDU = "baidu"
    SEARXNG = "searxng"


BAIDU_REMOVED = "Baidu search has been removed; switch this plan to SearXNG and save it"


class SearchPlan(BaseModel):
    """A plan as saved and as submitted; unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    topic: str
    provider: Provider
    queries: list[str]
    start_date: str
    end_date: str
    language: str
    max_results: Annotated[StrictInt, Field(ge=0)]
    # Sent as words with every query and listed for checking
    # in the results, since no search request can filter on them.
    region: str = ""
    institution: str = ""

    def to_json(self) -> dict[str, Any]:
        """The plan as stored (declaration order). An empty region
        or institution is left out, so plans saved before they existed still compare
        equal to their resubmitted form (`validate_saved`)."""
        value = self.model_dump(mode="json")
        for name in ("region", "institution"):
            if not value[name]:
                del value[name]
        return value

    def dumps(self) -> str:
        return jsonfmt.dumps(self.to_json(), sort_keys=False)

    def validated(self) -> SearchPlan:
        """Trimmed names and queries, or `HttpError(400)` naming what to fix."""
        try:
            check_id(self.id)
        except TopicError as error:
            raise HttpError(400, str(error)) from None
        if self.provider == Provider.BAIDU:
            raise HttpError(400, BAIDU_REMOVED)
        name, topic = trim(self.name), trim(self.topic)
        if not visible(name, 120) or not visible(topic, 120):
            raise HttpError(400, "Search name and topic must contain 1–120 visible characters")
        if not 1 <= len(self.queries) <= 8:
            raise HttpError(400, "Use between 1 and 8 search queries")
        queries: list[str] = []
        for query in self.queries:
            query = trim(query)
            if not visible(query, 200) or query in queries:
                raise HttpError(400, "Use unique queries of 1–200 visible characters")
            query_syntax.parse(query)
            queries.append(query)
        region, institution = trim(self.region), trim(self.institution)
        for value in (region, institution):
            if value and (not visible(value, 60) or '"' in value or "“" in value or "”" in value):
                raise HttpError(400, "Region and institution allow up to 60 characters without quotes")
        start = parse_date(self.start_date)
        end = parse_date(self.end_date)
        if start > end or self.language not in ("zh-CN", "en") or not 1 <= self.max_results <= 20:
            raise HttpError(
                400, "Use an ordered date range, zh-CN or en, and 1–20 results per query"
            )
        return self.model_copy(
            update={
                "name": name,
                "topic": topic,
                "queries": queries,
                "region": region,
                "institution": institution,
            }
        )


def visible(value: str, limit: int) -> bool:
    return bool(value) and len(value) <= limit and not any(is_control(c) for c in value)


def parse_date(value: str) -> date:
    """A strict `YYYY-MM-DD` calendar date (`%Y-%m-%d` plus a round trip)."""
    if _DATE.fullmatch(value):
        try:
            return date(int(value[:4]), int(value[5:7]), int(value[8:]))
        except ValueError:
            pass
    raise HttpError(400, "Use dates in YYYY-MM-DD format")


def field(value: Any, key: str) -> Any:
    """`value[key]` on parsed JSON: None for a missing key or a non-object."""
    return value.get(key) if isinstance(value, dict) else None


def count(value: Any) -> int:
    """A non-negative integer count; anything else counts as 0."""
    return value if type(value) is int and value >= 0 else 0


_DETAILS = (
    "p.config,p.revision,p.last_checked,p.last_status,"
    "(SELECT count(*) FROM search_materials WHERE plan_id=p.id)"
)


def _loads(text: str) -> Any:
    try:
        return jsonfmt.loads(text)
    except ValueError:
        return None


def _detail(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "config": _loads(row[0]),
        "revision": str(row[1]),
        "last_checked": row[2],
        "last_status": row[3],
        "item_count": row[4],
    }


def get(conn: sqlite3.Connection, id: str) -> dict[str, Any]:
    """The plan with its revision (a string), last check and material count; 404 if absent."""
    row = conn.execute(f"SELECT {_DETAILS} FROM search_plans p WHERE id=?1", (id,)).fetchone()
    if row is None:
        raise HttpError(404, "Search plan not found")
    return _detail(row)


def list_plans(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every plan as `get` returns it, by id."""
    return [_detail(r) for r in conn.execute(f"SELECT {_DETAILS} FROM search_plans p ORDER BY id")]


# Reclassify saved materials against a changed window, keeping their evidence.
_REWINDOW = """UPDATE search_materials SET content=json_set(content,'$.start_date',?2,'$.end_date',?3,'$.date_status',
                CASE WHEN json_extract(content,'$.date_status') IN ('in_range','out_of_range')
                    AND date(substr(json_extract(content,'$.published_at'),1,10)) IS NOT NULL
                THEN CASE WHEN substr(json_extract(content,'$.published_at'),1,10) BETWEEN ?2 AND ?3 THEN 'in_range' ELSE 'out_of_range' END
                ELSE coalesce(json_extract(content,'$.date_status'),'unknown') END) WHERE plan_id=?1"""


def save(
    conn: sqlite3.Connection, plan: SearchPlan, revision: str | None, id: str | None
) -> dict[str, Any]:
    """Create (`id` None, no revision) or update plan `id` at `revision`; returns `get`."""
    plan = plan.validated()
    if id is not None and id != plan.id:
        raise HttpError(400, "Search plan ID cannot change")
    with db.immediate(conn):
        running = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM source_jobs WHERE state='running')"
        ).fetchone()[0]
        if running:
            raise HttpError(
                409, "Wait for the current collection to finish before editing search plans"
            )
        if id is not None:
            old = get(conn, id)
            if revision != old["revision"]:
                raise HttpError(409, "Search plan changed; reload before saving")
            config = old["config"]
            if count(old["item_count"]) > 0 and field(config, "topic") != plan.topic:
                raise HttpError(409, "This plan has material. Create a new plan to change its topic")
            changed = conn.execute(
                "UPDATE search_plans SET config=?2,revision=revision+1 WHERE id=?1 AND revision=?3",
                (id, plan.dumps(), revision),
            ).rowcount
            if changed != 1:
                raise HttpError(409, "Search plan changed; reload before saving")
            if field(config, "start_date") != plan.start_date or field(config, "end_date") != plan.end_date:
                conn.execute(_REWINDOW, (plan.id, plan.start_date, plan.end_date))
        else:
            if revision is not None:
                raise HttpError(400, "New search plans must not specify a revision")
            if conn.execute("SELECT 1 FROM search_plans WHERE id=?1", (plan.id,)).fetchone():
                raise HttpError(409, "Search plan ID already exists")
            conn.execute("INSERT INTO search_plans(id,config) VALUES (?1,?2)", (plan.id, plan.dumps()))
    return get(conn, plan.id)


def validate_saved(conn: sqlite3.Connection, plan: SearchPlan) -> SearchPlan:
    """The validated plan, which must equal the saved one (a run uses saved plans only)."""
    plan = plan.validated()
    if get(conn, plan.id)["config"] != plan.to_json():
        raise HttpError(409, "Save the search plan before running it, or reload the current version")
    return plan
