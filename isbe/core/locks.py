"""Cross-process locks.

Both are `flock(LOCK_EX | LOCK_NB)` on a file in the root data directory, the
same lock the earlier Rust build took, so the CLI and either server exclude
each other. A lock file is never removed: unlinking it
while another process holds it would let a third process lock a new inode.
"""

import errno
import fcntl
import os
from pathlib import Path
from types import TracebackType

from isbe.core import paths

EXECUTION = ".execution.lock"
WORKER = ".queue-worker.lock"


class LockBusy(BlockingIOError):
    """Another process (or another handle in this one) holds the lock."""

    def __init__(self, message: str) -> None:
        super().__init__(errno.EWOULDBLOCK, message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class Lock:
    """A held lock; released by `release()`, the `with` block or garbage collection."""

    def __init__(self, path: Path, fd: int) -> None:
        self.path = path
        self._fd: int | None = fd

    def release(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            os.close(fd)  # closing the descriptor drops the flock

    def __enter__(self) -> Lock:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

    def __del__(self) -> None:
        self.release()


def lock_file(root: Path, name: str, busy: str) -> Lock:
    path = root / name
    paths.check_path(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise LockBusy(busy) from None
    except BaseException:
        os.close(fd)
        raise
    return Lock(path, fd)


def lock_root(data: Path) -> Path:
    """The root data directory for `data`, which may be `<root>/workspaces/<id>`."""
    owner = data.name
    if owner != "local" and paths.owner_valid(owner) and data.parent.name == "workspaces":
        return data.parent.parent
    return data


def execution_lock(data: Path) -> Lock:
    """Hold for an entire CLI mutation or report run; every workspace shares the root's lock."""
    root = paths.workspace(lock_root(data), "local")
    return lock_file(root, EXECUTION, "Another report or CLI operation is running")


def worker_lock(root: Path) -> Lock:
    """The single queue worker of a root data directory."""
    root = paths.workspace(root, "local")
    return lock_file(root, WORKER, "Another queue worker is already running")
