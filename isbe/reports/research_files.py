"""Research bundle files: identifiers, contained paths, bounded reads, write-once files.

A research bundle freezes operator-supplied originals under
`research/<topic>/<period>/`. Every path is built from a checked relative name,
and no ancestor may be a link, so neither the manifest nor a later request can
point a read or a write outside its directory.
"""

import hashlib
import os
import stat
from pathlib import Path

from isbe.core import validators

MAX_ORIGINAL = 10 * 1024 * 1024
MAX_TEXT = 200 * 1024
MAX_MANIFEST = 1024 * 1024


class ResearchError(ValueError):
    """A research input, bundle or model output that cannot be used."""


def os_error(error: OSError) -> ResearchError:
    """The operating-system error text, e.g. "File exists (os error 17)", without the path Python adds."""
    if error.errno is None:
        return ResearchError(str(error))
    return ResearchError(f"{os.strerror(error.errno)} (os error {error.errno})")


def sha256(data: bytes) -> str:
    """Lowercase hex SHA-256 of raw bytes (`db.sha256` hashes text)."""
    return hashlib.sha256(data).hexdigest()


def device_name(value: str) -> bool:
    """A Windows device name (CON, PRN, AUX, NUL, COM1-9, LPT1-9) as the first dot-part."""
    stem = validators.ascii_lower(value.split(".", 1)[0]).encode()
    return stem in (b"con", b"prn", b"aux", b"nul") or (
        len(stem) == 4 and stem[:3] in (b"com", b"lpt") and 0x31 <= stem[3] <= 0x39
    )


def identifier(value: str) -> bool:
    """A topic, period, source or evidence id: 1-96 of `A-Za-z0-9-_.`, no dot run or edge dot."""
    return (
        value != ""
        and validators.byte_len(value) <= 96
        and not value.startswith(".")
        and not value.endswith(".")
        and ".." not in value
        and not device_name(value)
        and value.isascii()
        and all(c.isalnum() or c in "-_." for c in value)
    )


def no_links(path: Path) -> None:
    """Refuse a link at `path` or any ancestor; missing entries are fine."""
    for ancestor in (path, *path.parents):
        try:
            mode = os.lstat(ancestor).st_mode
        except FileNotFoundError:
            continue
        except OSError as error:
            raise os_error(error) from None
        except ValueError:
            # Python's own text for a NUL in a path differs; this wording is reported.
            raise ResearchError("file name contained an unexpected NUL byte") from None
        if stat.S_ISLNK(mode):
            raise ResearchError("research paths must not contain links")


def _normal_components(name: str) -> list[str] | None:
    """The path's components when every one is a normal name, else None.

    Repeated and trailing separators vanish and an inner `.` is skipped; a leading `/`, a leading `.` or any `..` is not a normal component.
    """
    if name.startswith("/"):
        return None
    parts = name.split("/")
    if parts[0] == ".":
        return None
    normal = [part for part in parts if part not in ("", ".")]
    return None if ".." in normal else normal


def child(root: Path, name: str) -> Path:
    """`root/name` for a relative `name` that stays inside `root` and crosses no link."""
    components = _normal_components(name) if name else None
    if (
        components is None
        or "\\" in name
        or ":" in name
        or any(part.endswith((".", " ")) or device_name(part) for part in components)
    ):
        raise ResearchError(
            "research input paths must be relative and stay inside their directory"
        )
    path = root.joinpath(*components)
    no_links(path)
    return path


def extension(name: str) -> str | None:
    """The extension of a checked relative name (none for dot-files)."""
    file = [part for part in name.split("/") if part not in ("", ".")][-1]
    if file == ".." or "." not in file:
        return None
    before, after = file.rsplit(".", 1)
    return None if before == "" else after


def read(path: Path, limit: int) -> bytes:
    """A regular file of at most `limit` bytes."""
    no_links(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    except OSError as error:
        raise ResearchError("read research file") from os_error(error)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ResearchError("research input must be a regular file")
        chunks, size = [], 0
        while size <= limit:
            chunk = os.read(fd, limit + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError as error:
        raise os_error(error) from None
    finally:
        os.close(fd)
    if size > limit:
        raise ResearchError("research file exceeds its size limit")
    return b"".join(chunks)


def mkdir(path: Path) -> None:
    no_links(path)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as error:
        raise os_error(error) from None
    no_links(path)


def write_new(path: Path, data: bytes) -> None:
    """Create `path` with `data`, failing if anything already holds the name.

    Not `paths.atomic_write`: these files are write-once, created in a
    directory this run reserved, with the default permissions.
    """
    no_links(path)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o666)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as error:
        raise os_error(error) from None
