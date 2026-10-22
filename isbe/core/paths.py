"""Data-directory paths: owner workspaces, symlink refusal and atomic writes.

Covers `owner_valid`, `check_path`, `private_dir`, `check_tree` and `workspace`.
The owner of a workspace always comes from
authentication, never from a client-supplied path, and nothing under the data
directory may be a symlink: a link could point a report, figure or database
write outside it.
"""

import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# Names a workspace may hold that must never be links.
_CHECKED = (
    "topics",
    "reports",
    "figures",
    "memory",
    "isbe.db",
    "isbe.db-wal",
    "isbe.db-shm",
    "settings.json",
)


class UnsafePath(ValueError):
    """A path under the data directory that is a link, not a directory, or not an owner."""


def owner_valid(owner: str) -> bool:
    """`local`, or an account id: 32 lowercase hex characters."""
    return owner == "local" or (
        len(owner) == 32 and all(c in "0123456789abcdef" for c in owner)
    )


def check_path(path: Path) -> None:
    """Refuse a symlink; a missing path is fine."""
    try:
        if stat.S_ISLNK(os.lstat(path).st_mode):
            raise UnsafePath("workspace links are not allowed")
    except FileNotFoundError:
        pass


def private_dir(path: Path) -> None:
    """Create `path` as a 0700 directory if missing; it must be a real directory."""
    check_path(path)
    if not path.exists():
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    check_path(path)
    if not path.is_dir():
        raise UnsafePath("workspace must be a directory")


def check_tree(path: Path) -> None:
    """Refuse a symlink anywhere at or below `path`."""
    check_path(path)
    if path.is_dir():
        for entry in path.iterdir():
            check_tree(entry)


def workspace(root: Path, owner: str) -> Path:
    """The canonical data directory of `owner` under `root`, created if needed.

    `local` is the root itself; an account lives in `workspaces/<id>`. Every
    ancestor of the root and every well-known entry must not be a link, and the
    topics directory is created private.
    """
    if not owner_valid(owner):
        raise UnsafePath("invalid workspace owner")
    for ancestor in (root, *root.parents):
        check_path(ancestor)
    root.mkdir(parents=True, exist_ok=True)
    check_path(root)
    root = root.resolve(strict=True)
    if owner == "local":
        data = root
    else:
        every = root / "workspaces"
        private_dir(every)
        data = every / owner
        private_dir(data)
    for name in _CHECKED:
        check_path(data / name)
    private_dir(data / "topics")
    return data


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class Staged:
    """A fsynced temporary file beside `path`, not yet visible under that name.

    `commit()` moves it into place: `os.replace` (atomic replacement), or with
    `create_new` a hard link that fails with `FileExistsError` instead of
    replacing an existing file. Leaving `staged_write` without committing
    removes the temporary.
    """

    def __init__(self, path: Path, temp: Path, create_new: bool) -> None:
        self.path = path
        self.temp = temp
        self.create_new = create_new
        self.committed = False

    def commit(self) -> None:
        if self.committed:
            return
        check_path(self.path)
        if self.create_new:
            os.link(self.temp, self.path)
            os.unlink(self.temp)
        else:
            os.replace(self.temp, self.path)
        self.committed = True
        _fsync_dir(self.path.parent)

    def discard(self) -> None:
        if not self.committed:
            try:
                os.unlink(self.temp)
            except FileNotFoundError:
                pass


@contextmanager
def staged_write(
    path: Path, data: bytes | str, *, mode: int = 0o600, create_new: bool = False
) -> Iterator[Staged]:
    """Write `data` to a temporary beside `path`; the caller decides whether to `commit()`.

    For read-check-replace updates: stage the new bytes, re-check a revision
    (under the caller's own lock), then commit; any exception or a block that
    does not commit leaves `path` untouched and removes the temporary.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    path = Path(path)
    check_path(path)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    # A unique name: a crashed writer leaves an orphan that never blocks the next one.
    temp = parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(temp, flags, mode)
    staged = Staged(path, temp, create_new)
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)
        yield staged
    finally:
        staged.discard()


def atomic_write(
    path: Path, data: bytes | str, *, mode: int = 0o600, create_new: bool = False
) -> None:
    """Replace (or, with `create_new`, create without clobbering) `path` atomically.

    The bytes go to a uniquely named temporary file beside the target, created
    exclusively with `mode`, fsynced and then renamed (or hard-linked, for
    `create_new`) into place, so readers see the old or the new file and a crash
    leaves at most an orphaned temporary that blocks nothing. Raises
    `FileExistsError` when `create_new` finds the target, `UnsafePath` when the
    target is a symlink.
    """
    with staged_write(path, data, mode=mode, create_new=create_new) as staged:
        staged.commit()
