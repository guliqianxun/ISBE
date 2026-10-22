"""Media report files: link refusal, bounded reads, write-once files and the frozen snapshot.

A media report is `reports/<topic>/<period>.md` plus the snapshot it was
rendered from, `reports/<topic>/<period>.sources.json`, whose SHA-256 the
report's fingerprint records. Both are created new (never replaced) and are
removed again if the report row is not committed. Article identities are
resolved only from a snapshot that still matches its fingerprint.
"""

import os
import sqlite3
import stat
from pathlib import Path
from typing import Any

from isbe.core import db, jsonfmt
from isbe.core.errors import HttpError
from isbe.reports.research_files import identifier

MAX_SNAPSHOT = 32 * 1024 * 1024

__all__ = [
    "MAX_SNAPSHOT", "Created", "MediaError", "absent", "identifier", "no_links", "os_error",
    "read_text", "snapshot", "try_exists",
]


class MediaError(ValueError):
    """A media report, snapshot or input that cannot be used."""


def os_error(error: OSError) -> MediaError:
    """The operating-system error text, e.g. "File exists (os error 17)", without the path Python adds."""
    if error.errno is None:
        return MediaError(str(error))
    return MediaError(f"{os.strerror(error.errno)} (os error {error.errno})")


def no_links(path: Path) -> None:
    """Refuse a link at `path` or any ancestor; missing entries are fine."""
    for ancestor in (path, *path.parents):
        try:
            mode = os.lstat(ancestor).st_mode
        except FileNotFoundError:
            continue
        except OSError as error:
            raise os_error(error) from None
        if stat.S_ISLNK(mode):
            raise MediaError("Report paths must not contain links")


def absent(path: Path) -> bool:
    """Whether `lstat` finds nothing at `path`, not even a link."""
    try:
        os.lstat(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def try_exists(path: Path) -> bool:
    """Whether `path` exists, following links; only NotFound means absent, other errors raise."""
    try:
        os.stat(path)
    except FileNotFoundError:
        return False
    except OSError as error:
        raise os_error(error) from None
    return True


def read_text(path: Path, limit: int, too_large: str) -> str:
    """The file as UTF-8 text, reading at most `limit + 1` bytes; more than `limit` is refused with `too_large`."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    except OSError as error:
        raise os_error(error) from None
    try:
        chunks, size = [], 0
        while size <= limit:
            chunk = os.read(fd, min(limit + 1 - size, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError as error:
        raise os_error(error) from None
    finally:
        os.close(fd)
    try:
        text = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError:
        raise MediaError("stream did not contain valid UTF-8") from None
    if size > limit:
        raise MediaError(too_large)
    return text


class Created:
    """Files this operation created, removed again unless `committed` is set."""

    def __init__(self) -> None:
        self.paths: list[Path] = []
        self.committed = False

    def write(self, path: Path, data: bytes) -> None:
        """Create `path` with `data`; anything already at the name, a link included, fails."""
        no_links(path)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o666)
        except OSError as error:
            raise os_error(error) from None
        self.paths.append(path)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        except OSError as error:
            raise os_error(error) from None
        finally:
            os.close(fd)

    def discard(self) -> None:
        if self.committed:
            return
        for path in reversed(self.paths):
            try:
                os.remove(path)
            except OSError:
                pass


def _same(a: Any, b: Any) -> bool:
    """JSON equality: `1` is not `1.0` and `true` is not `1`."""
    return jsonfmt.dumps(a) == jsonfmt.dumps(b)


def _inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def snapshot(
    conn: sqlite3.Connection, data: Path, topic: str, period: str
) -> dict[str, Any] | None:
    """The verified frozen snapshot of the media report `topic`/`period`, or None.

    None when no weekly report of that period exists or it is not a media
    report. Raises `HttpError(400)` for an invalid topic or period, and
    `MediaError` (or the `ValueError` of unparseable JSON) when the row's
    paths, the snapshot's hash, its identity or its material identities do not
    match what was recorded when the report was frozen.
    """
    if not identifier(topic) or not identifier(period):
        raise HttpError(400, "Invalid report identifier")
    row = conn.execute(
        "SELECT path,fingerprint FROM reports WHERE topic_id=?1 AND period=?2 AND kind='weekly'",
        (topic, period),
    ).fetchone()
    if row is None:
        return None
    report_path, fingerprint = row[0], jsonfmt.loads(row[1])
    if not isinstance(fingerprint, dict) or fingerprint.get("mode") != "media-weekly":
        return None
    relative = f"reports/{topic}/{period}.sources.json"
    if (
        report_path != f"reports/{topic}/{period}.md"
        or fingerprint.get("snapshot_path") != relative
    ):
        raise MediaError("Invalid report snapshot path")
    path = data / relative
    no_links(path)
    try:
        reports = (data / "reports").resolve(strict=True)
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise os_error(error) from None
    if not _inside(resolved, reports):
        raise MediaError("Invalid report snapshot path")
    text = read_text(path, MAX_SNAPSHOT, "Report snapshot is too large")
    if fingerprint.get("snapshot_sha256") != db.sha256(text):
        raise MediaError("Report snapshot has changed")
    frozen = jsonfmt.loads(text)
    if not (
        isinstance(frozen, dict)
        and frozen.get("mode") == "media-weekly"
        and frozen.get("topic") == topic
        and frozen.get("period") == period
    ):
        raise MediaError("Report snapshot does not match this report")
    materials = frozen.get("materials")
    if not isinstance(materials, list):
        raise MediaError("Missing report materials")
    ids = [item.get("id") if isinstance(item, dict) else None for item in materials]
    if not _same(fingerprint.get("materials"), ids):
        raise MediaError("Report material identities have changed")
    seen: set[str] = set()
    for item, id in zip(materials, ids, strict=True):
        if not isinstance(id, str) or not id:
            raise MediaError("Invalid report material identity")
        if not isinstance(item.get("url"), str):
            raise MediaError("Missing report article URL")
        if id in seen:
            raise MediaError("Duplicate report material identity")
        seen.add(id)
    return frozen
