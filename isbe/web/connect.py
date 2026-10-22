"""Database connections for request handlers."""

import sqlite3
from pathlib import Path

from isbe.core import db


def _lossy(raw: bytes) -> str:
    # Stored text is read leniently: a bad byte is not a 500.
    return raw.decode("utf-8", "replace")


def open_db(data: Path) -> sqlite3.Connection:
    """`data/isbe.db`, opened (and migrated) for one request; the caller closes it."""
    conn = db.connect(data / "isbe.db")
    conn.text_factory = _lossy
    return conn
