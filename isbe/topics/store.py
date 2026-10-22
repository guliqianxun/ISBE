"""Editable topic files. The submitted document stays separate from the runtime
Topic so defaults and expanded environment variables never leak into YAML.

Writes are serialized by a process-wide lock; every write also checks the
file's revision (SHA-256 of its bytes) again after staging the replacement,
so a manual edit made during validation is never overwritten.
"""

import errno
import hashlib
import logging
import os
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from isbe.core import paths, settings
from isbe.core.errors import HttpError

from . import model, yamlio
from .decode import parse_bool

log = logging.getLogger(__name__)

MAX_BYTES = 64 * 1024
_WRITES = threading.Lock()
_REPARSE_POINT = 0x400
_NOT_REGULAR = "topic must be a regular file, not a directory or link"
_CHANGED = "topic changed since it was loaded; reload before saving or deleting"


def status(error: BaseException) -> int:
    """HTTP status of a store error; anything that is not an `HttpError` is internal."""
    return error.status if isinstance(error, HttpError) else 500


def _valid_id(id: str) -> None:
    try:
        model.check_id(id)
    except model.TopicError as e:
        raise HttpError(400, str(e)) from None


def _linked(meta: os.stat_result) -> bool:
    """Do not follow symlinks or Windows junctions/reparse points."""
    return stat.S_ISLNK(meta.st_mode) or bool(getattr(meta, "st_file_attributes", 0) & _REPARSE_POINT)


def _metadata(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _directory(data: Path, create: bool) -> Path | None:
    dir = data / "topics"
    if create and _metadata(dir) is None:
        dir.mkdir(parents=True, exist_ok=True)
    meta = _metadata(dir)
    if meta is None:
        return None
    if _linked(meta) or not stat.S_ISDIR(meta.st_mode):
        raise HttpError(400, "topics must be a directory, not a link or another file type")
    return dir


def _existing(dir: Path, id: str) -> Path | None:
    yaml, yml = dir / f"{id}.yaml", dir / f"{id}.yml"
    yaml_meta, yml_meta = _metadata(yaml), _metadata(yml)
    if yaml_meta is not None and yml_meta is not None:
        raise HttpError(409, "both .yaml and .yml exist for this topic; remove the duplicate file first")
    path, meta = (yaml, yaml_meta) if yaml_meta is not None else (yml, yml_meta)
    if meta is None:
        return None
    if not stat.S_ISREG(meta.st_mode) or _linked(meta):
        raise HttpError(400, _NOT_REGULAR)
    return path


def _locate(data: Path, id: str) -> Path:
    _valid_id(id)
    dir = _directory(data, False)
    path = _existing(dir, id) if dir is not None else None
    if path is None:
        raise HttpError(404, "topic not found")
    return path


@dataclass(frozen=True)
class _Document:
    content: bytes | None  # None means oversized; hashing still streams so a bad file can be deleted.
    revision: str


def _read(path: Path) -> _Document:
    meta = _metadata(path)
    if meta is None:
        raise HttpError(404, "topic not found")
    if _linked(meta) or not stat.S_ISREG(meta.st_mode):
        raise HttpError(400, _NOT_REGULAR)
    # No-follow and non-blocking close the gap between the check and the open.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise HttpError(404, "topic not found") from None
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise HttpError(400, _NOT_REGULAR) from None
        raise
    with os.fdopen(fd, "rb") as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise HttpError(400, _NOT_REGULAR)
        digest = hashlib.sha256()
        buffer: bytearray | None = bytearray()
        while chunk := file.read(8192):
            digest.update(chunk)
            if buffer is not None:
                if len(buffer) + len(chunk) <= MAX_BYTES:
                    buffer += chunk
                else:
                    buffer = None
    return _Document(bytes(buffer) if buffer is not None else None, digest.hexdigest())


def _validate(data: Path, id: str, text: str) -> None:
    try:
        saved = settings.read(data)
    except Exception as e:
        raise RuntimeError("read settings to validate a topic") from e

    def env(name: str) -> str | None:
        value = os.environ.get(name)
        return value if value and value.strip() else saved.get(name)

    try:
        model.parse_with_env(text, id, env)
    except model.TopicError as e:
        raise HttpError(400, str(e)) from None


def _detail(data: Path, id: str, doc: _Document) -> dict[str, Any]:
    result: dict[str, Any] = {"id": id, "config": None, "revision": doc.revision}
    if doc.content is None:
        result["error"] = "topic file exceeds 64 KiB"
        return result
    try:
        text = doc.content.decode("utf-8")
    except UnicodeDecodeError:
        result["error"] = "topic file is not valid UTF-8"
        return result
    try:
        result["config"] = yamlio.load_value(text)
    except yamlio.YamlError:
        result["error"] = "topic file contains invalid YAML"
        return result
    try:
        _validate(data, id, text)
    except HttpError as e:
        result["error"] = e.message
    except Exception:
        log.exception("validate topic %s", id)
        result["error"] = "Topic settings could not be read. Review Settings and try again."
    return result


def _lossy(name: str) -> str:
    """`to_string_lossy` for a file name decoded with surrogateescape."""
    return name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def list_topics(data: Path) -> list[dict[str, Any]]:
    """One row per topic file; a malformed file is a row with an error, not a failure of the list."""
    dir = _directory(data, False)
    if dir is None:
        return []
    ids = sorted({_lossy(Path(n).stem) for n in os.listdir(dir) if Path(n).suffix in (".yaml", ".yml")})
    rows = []
    for id in ids:
        try:
            doc = get(data, id)
        except Exception as e:
            if status(e) == 500:
                log.exception("read topic %s", id)
                message = "Topic could not be read. Try again or review the service logs."
            else:
                message = str(e)
            doc = {"error": message, "revision": ""}
        config = doc.get("config")
        config = config if isinstance(config, dict) else {}
        label = config.get("label")
        active = config.get("active")
        if isinstance(active, str):
            # The raw view is YAML 1.2, where `active: no` is a string; the
            # Topic reads it as false, so the list must too.
            active = parse_bool(active)
        row = {
            "id": id,
            "label": label if isinstance(label, str) and label.strip() else id,
            "active": active if isinstance(active, bool) else True,
            "revision": doc["revision"],
        }
        if "error" in doc:
            row["error"] = doc["error"]
        rows.append(row)
    return rows


def get(data: Path, id: str) -> dict[str, Any]:
    """The raw document as `{id, config, revision[, error]}`, never the
    expanded and default-filled runtime Topic."""
    return _detail(data, id, _read(_locate(data, id)))


def _encode(data: Path, id: str, config: Any) -> str:
    _valid_id(id)
    if not isinstance(config, dict) or config.get("id") != id:
        raise HttpError(400, "topic id cannot be changed")
    try:
        text = yamlio.dump(config)
    except (yamlio.YamlError, TypeError, ValueError):
        raise HttpError(400, "cannot serialize topic configuration") from None
    if len(text.encode()) > MAX_BYTES:
        raise HttpError(400, "topic configuration exceeds 64 KiB")
    _validate(data, id, text)
    return text


def _check_revision(path: Path, revision: str) -> None:
    if not revision or _read(path).revision != revision:
        raise HttpError(409, _CHANGED)


def create(data: Path, config: Any) -> dict[str, Any]:
    """Write a new topic; 409 if one with that id exists, even when racing another create."""
    id = config.get("id") if isinstance(config, dict) else None
    if not isinstance(id, str):
        raise HttpError(400, "topic id is required")
    text = _encode(data, id, config)
    with _WRITES:
        dir = _directory(data, True)
        if dir is None:
            raise RuntimeError("topics directory disappeared")
        if _existing(dir, id) is not None:
            raise HttpError(409, "topic already exists")
        with paths.staged_write(dir / f"{id}.yaml", text, create_new=True) as staged:
            if _existing(dir, id) is not None:
                raise HttpError(409, "topic already exists")
            # Publish a complete file without ever replacing an existing destination.
            try:
                staged.commit()
            except FileExistsError:
                raise HttpError(409, "topic already exists") from None
    return get(data, id)


def update(data: Path, id: str, config: Any, revision: str) -> dict[str, Any]:
    """Replace the file (keeping its .yaml/.yml name) if it still has `revision`."""
    text = _encode(data, id, config)
    with _WRITES:
        path = _locate(data, id)
        _check_revision(path, revision)
        with paths.staged_write(path, text) as staged:
            # Recheck after staging the replacement to catch manual edits during validation.
            if _locate(data, id) != path:
                raise HttpError(409, "topic file moved; reload before saving")
            _check_revision(path, revision)
            staged.commit()
    return get(data, id)


def delete(data: Path, id: str, revision: str) -> None:
    """Remove the topic file only; reports, items and memory stay."""
    with _WRITES:
        path = _locate(data, id)
        _check_revision(path, revision)
        path.unlink()
