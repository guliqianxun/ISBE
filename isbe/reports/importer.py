"""Reports written elsewhere, such as the Python prototype's `artifacts/`:
`<dir>/<topic>/<period>/latest.md` (or the newest
`.md` there) becomes `reports/<topic>/<period>.md` and a `reports` row dated
by the file. Inline base64 figures move to `figures/`. Where ISBE has its own
report for the period, the import lands beside it as `<period>-v1`, so the
two can be compared. A path that belongs to another kind of report, such as
a research report, is refused.
"""

import base64
import binascii
import os
import re
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from isbe.core import jsonfmt, paths
from isbe.pipeline.digest import store
from isbe.pipeline.digest.parse import trim


class ImportFailed(Exception):
    pass


def run(conn: sqlite3.Connection, data: Path, src: str | os.PathLike[str], stats: dict[str, Any]) -> None:
    """Import every `<src>/<topic>/<period>` report; fills `stats` with
    imported, beside_own, figures and (when any) skipped.

    Pass `src` as the user typed it: `imported_from` records the joined path
    verbatim, and `pathlib.Path` would normalize it.
    """
    root = os.fspath(src)
    imported = beside = figures = 0
    skipped: list[str] = []
    try:
        topics = _entries(root)
    except OSError as e:
        raise ImportFailed(f"read {root}") from e
    for topic_id, topic_path in topics:
        if not os.path.isdir(topic_path) or not _safe(topic_id):
            continue
        for period, period_path in _entries(topic_path):
            file = _newest_report(period_path)
            if file is None or not _safe(period):
                continue
            kind = _kind(period)
            if kind is None:
                skipped.append(f"{topic_id}/{period}")
                continue
            existing = conn.execute(
                "SELECT fingerprint FROM reports WHERE topic_id = ?1 AND period = ?2 AND kind = ?3",
                (topic_id, period, kind),
            ).fetchone()
            own = existing is not None and '"imported_from"' not in existing[0]
            if own:
                beside += 1
            label = f"{period}-v1" if own else period
            # Before any figure is moved; `store.save` checks again under its lock.
            store.check_free(conn, data, topic_id, label, kind)
            try:
                md = Path(file).read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError) as e:
                raise ImportFailed(f"read {file}") from e
            md, n = _move_figures(md, data, f"v1-{topic_id}-{label}")
            figures += n
            store.save(
                conn,
                data,
                report_id=f"{topic_id}/{label}/{kind}",
                topic=topic_id,
                period=label,
                kind=kind,
                body=md,
                fingerprint=jsonfmt.dumps({"imported_from": file}),
                created_at=_mtime(file),
            )
            imported += 1
    stats["imported"] = imported
    stats["beside_own"] = beside
    stats["figures"] = figures
    if skipped:
        stats["skipped"] = skipped


def _entries(directory: str) -> list[tuple[str, str]]:
    """(name, path) of a directory's entries by name; paths joined as `read_dir` does."""
    with os.scandir(directory) as it:
        return sorted(((e.name, e.path) for e in it), key=lambda e: os.fsencode(e[0]))


def _extension(name: str) -> str | None:
    """The extension after the last dot; none for a dotfile such as `.md`."""
    i = name.rfind(".")
    return name[i + 1 :] if i > 0 else None


def _newest_report(directory: str) -> str | None:
    """A period directory's report: `latest.md`, else (older layouts) its newest
    `.md` file. Superseded renders in `.history/` are not looked at."""
    latest = os.path.join(directory, "latest.md")
    if os.path.isfile(latest):
        return latest
    try:
        with os.scandir(directory) as it:
            candidates = [e.path for e in it if _extension(e.name) == "md"]
    except OSError:
        return None
    best: str | None = None
    best_key = (False, 0)
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            key = (True, os.stat(path).st_mtime_ns)
        except OSError:
            key = (False, 0)  # sorts before any time
        # Of several equal times the last file wins.
        if best is None or key >= best_key:
            best, best_key = path, key
    return best


_NAME = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _safe(name: str) -> bool:
    """Names that end up in file paths: letters, digits, `.`, `_`, `-`, no leading dot."""
    return bool(name) and not name.startswith(".") and all(c in _NAME for c in name)


_DAY = re.compile(r"(\d{4})-(\d{2})-(\d{2})", re.ASCII)


def _is_date(text: str) -> bool:
    m = _DAY.fullmatch(text)
    if m is None:
        return False
    try:
        date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return False
    return True


def _kind(period: str) -> str | None:
    """2026-W41 weekly, 2026-10-12 daily, 2026-10 monthly."""
    if "-W" in period:
        return "weekly"
    if len(period) == 10 and _is_date(period):
        return "daily"
    if len(period) == 7 and _is_date(f"{period}-01"):
        return "monthly"
    return None


def _mtime(file: str) -> str:
    """The file's modification time, RFC 3339 in UTC with milliseconds (truncated, not rounded)."""
    seconds, nanos = divmod(os.stat(file).st_mtime_ns, 1_000_000_000)
    stamp = datetime.fromtimestamp(seconds, UTC)
    return f"{stamp:%Y-%m-%dT%H:%M:%S}.{nanos // 1_000_000:03}Z"


_TYPES = {
    "data:image/png": "png",
    "data:image/jpeg": "jpg",
    "data:image/jpg": "jpg",
    "data:image/webp": "webp",
}


def _decode(uri: str) -> tuple[str, bytes] | None:
    """A `data:image/…;base64,…` URI as (extension, bytes), canonical base64 only."""
    head, sep, b64 = uri.partition(";base64,")
    ext = _TYPES.get(head) if sep else None
    if ext is None:
        return None
    b64 = trim(b64)
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return None
    # Canonical padding and zero trailing bits only.
    if base64.b64encode(raw).decode("ascii") != b64:
        return None
    return ext, raw


def _move_figures(md: str, data: Path, stem: str) -> tuple[str, int]:
    """Write each `](data:image/…;base64,…)` image to `figures/<stem>-<n>.<ext>`
    and link it the way ISBE's reports do. One that does not decode stays."""
    out: list[str] = []
    rest = md
    n = 0
    while (i := rest.find("](data:image/")) >= 0:
        start = i + 2
        out.append(rest[:start])
        uri = rest[start:]
        end = uri.find(")")
        if end < 0:
            end = len(uri)
        decoded = _decode(uri[:end])
        if decoded is None:
            out.append(uri[:end])
        else:
            ext, raw = decoded
            name = f"{stem}-{n}.{ext}"
            directory = data / "figures"
            paths.check_path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            # 0644: the usual mode for a figure under the default umask.
            paths.atomic_write(directory / name, raw, mode=0o644)
            out.append(f"../../figures/{name}")
            n += 1
        rest = uri[end:]
    out.append(rest)
    return "".join(out), n
