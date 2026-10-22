"""Source definitions.

No model calls or editorial gates. A definition is stored as compact JSON with its
fields in declaration order; its revision is an integer the API exchanges as a
string.
"""

import sqlite3
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from isbe.core import db, jsonfmt
from isbe.core.errors import HttpError
from isbe.core.validators import byte_len, is_control, trim
from isbe.topics.model import TopicError, check_id

from . import reader
from .reader import Kind


def error(code: int, message: str) -> HttpError:
    """A failure shown to the user with HTTP status `code`."""
    return HttpError(code, message)


def status(e: BaseException) -> int:
    """The HTTP status of an error from these modules: its own, or 500."""
    return e.status if isinstance(e, HttpError) else 500


def message(e: BaseException) -> str:
    """The outermost message of an error, without its causes."""
    return str(e) or type(e).__name__


def checked_id(id: str) -> None:
    try:
        check_id(id)
    except TopicError as e:
        raise HttpError(400, str(e)) from None


class Source(BaseModel):
    """A configured source; unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    topic: str
    kind: Kind
    url: str
    enabled: StrictBool = True
    link_pattern: str = ""
    max_items: Annotated[StrictInt, Field(ge=0)] = 100

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def dumps(self) -> str:
        """The stored form: compact JSON, fields in declaration order."""
        return jsonfmt.dumps(self.to_json(), sort_keys=False)

    def validated(self) -> Source:
        """Trimmed name and topic and the canonical URL, or `HttpError(400)`."""
        checked_id(self.id)
        name, topic = trim(self.name), trim(self.topic)
        if any(not s or len(s) > 120 or any(is_control(c) for c in s) for s in (name, topic)):
            raise HttpError(400, "Source name and topic must contain 1–120 visible characters")
        url = reader.validate_url(self.url)  # HttpError(400)
        if (
            not 1 <= self.max_items <= 200
            or byte_len(self.link_pattern) > 512
            or any(is_control(c) for c in self.link_pattern)
        ):
            raise HttpError(400, "Use 1–200 items and a link pattern no longer than 512 bytes")
        return self.model_copy(update={"name": name, "topic": topic, "url": url})


DETAIL_COLUMNS = (
    "d.config,d.revision,d.origin,d.discovered_from,d.last_checked,d.last_status,d.last_error,"
    "(SELECT count(*) FROM source_materials WHERE source_id=d.id)"
)


def loads(text: str | None) -> Any:
    """Parsed JSON, or None when the text is missing or malformed."""
    if text is None:
        return None
    try:
        return jsonfmt.loads(text)
    except ValueError:
        return None


def detail(row: sqlite3.Row) -> dict[str, Any]:
    config = loads(row[0])
    url = config.get("url") if isinstance(config, dict) else None
    url = url if isinstance(url, str) else ""
    site = url.split("://", 1)[1].split("/", 1)[0] if "://" in url else ""
    return {
        "config": config,
        "site": site,
        "revision": str(row[1]),
        "origin": row[2],
        "discovered_from": row[3],
        "last_checked": row[4],
        "last_status": row[5],
        "last_error": row[6],
        "item_count": row[7],
    }


def get(conn: sqlite3.Connection, id: str) -> dict[str, Any]:
    """One source with its revision, origin, last check and material count; 404 if absent."""
    row = conn.execute(
        f"SELECT {DETAIL_COLUMNS} FROM source_definitions d WHERE id=?1", (id,)
    ).fetchone()
    if row is None:
        raise HttpError(404, "Source not found")
    return detail(row)


def list_sources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every source as `get` returns it, by id."""
    return [
        detail(r)
        for r in conn.execute(f"SELECT {DETAIL_COLUMNS} FROM source_definitions d ORDER BY id")
    ]


def idle(conn: sqlite3.Connection) -> None:
    running = conn.execute("SELECT count(*) FROM source_jobs WHERE state='running'").fetchone()[0]
    if running > 0:
        raise HttpError(409, "A source operation is running. Wait before changing its configuration.")


def insert(conn: sqlite3.Connection, source: Source, origin: str, seed: str | None) -> None:
    conn.execute(
        "INSERT INTO source_definitions(id,config,origin,discovered_from) VALUES (?1,?2,?3,?4)",
        (source.id, source.dumps(), origin, seed),
    )


def _field(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def save(
    conn: sqlite3.Connection, source: Source, revision: str | None, id: str | None
) -> dict[str, Any]:
    """Create a manual source (`id` None, no revision) or update `id` at `revision`."""
    source = source.validated()
    if id is not None and id != source.id:
        raise HttpError(400, "Source ID cannot change")
    with db.immediate(conn):
        idle(conn)
        if id is not None:
            old = get(conn, id)
            if revision != old["revision"]:
                raise HttpError(409, "Source changed; reload before saving")
            config = old["config"]
            if old["item_count"] > 0 and (
                _field(config, "url") != source.url
                or _field(config, "kind") != source.kind.value
                or _field(config, "topic") != source.topic
            ):
                raise HttpError(
                    409,
                    "This source has collected material. Add a new source to change its URL, type "
                    "or topic; existing history is retained.",
                )
            changed = conn.execute(
                "UPDATE source_definitions SET config=?2,revision=revision+1 WHERE id=?1 AND revision=?3",
                (id, source.dumps(), revision),
            ).rowcount
            if changed != 1:
                raise HttpError(409, "Source changed; reload before saving")
        else:
            if revision is not None:
                raise HttpError(400, "A new source must not specify a revision")
            if conn.execute("SELECT 1 FROM source_definitions WHERE id=?1", (source.id,)).fetchone():
                raise HttpError(409, "Source ID already exists")
            insert(conn, source, "manual", None)
    return get(conn, source.id)
