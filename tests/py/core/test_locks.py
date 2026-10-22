import subprocess
import sys
from pathlib import Path

import pytest

from isbe.core import locks, paths

OWNER = "0123456789abcdef0123456789abcdef"
PROBE = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(3)
"""


def locked_elsewhere(path: Path) -> bool:
    """Whether another process (as a second CLI would) fails to take the flock."""
    return subprocess.run([sys.executable, "-c", PROBE, str(path)]).returncode == 3


def test_execution_lock_is_shared_by_every_workspace_and_other_processes(tmp_path: Path) -> None:
    root = tmp_path / "data"
    other = paths.workspace(root, OWNER)
    held = locks.execution_lock(root)
    assert held.path == root.resolve() / ".execution.lock"
    assert locked_elsewhere(held.path)
    with pytest.raises(locks.LockBusy, match="Another report or CLI operation is running"):
        locks.execution_lock(root)
    with pytest.raises(locks.LockBusy):
        locks.execution_lock(other)
    held.release()
    assert held.path.exists(), "the lock file is never removed"
    assert not locked_elsewhere(held.path)
    with locks.execution_lock(other) as again:
        assert again.path == held.path
    assert not locked_elsewhere(held.path)


def test_worker_lock_is_separate_from_execution(tmp_path: Path) -> None:
    root = tmp_path / "data"
    worker = locks.worker_lock(root)
    assert worker.path.name == ".queue-worker.lock"
    with pytest.raises(locks.LockBusy, match="Another queue worker is already running") as caught:
        locks.worker_lock(root)
    assert isinstance(caught.value, BlockingIOError)
    with locks.execution_lock(root):
        pass
    worker.release()
    locks.worker_lock(root).release()


def test_lock_root_mapping(tmp_path: Path) -> None:
    root = tmp_path / "data"
    assert locks.lock_root(root / "workspaces" / OWNER) == root
    assert locks.lock_root(root / "workspaces" / "local") == root / "workspaces" / "local"
    assert locks.lock_root(root / "elsewhere" / OWNER) == root / "elsewhere" / OWNER
    assert locks.lock_root(root) == root


def test_linked_lock_files_are_refused(tmp_path: Path) -> None:
    root = paths.workspace(tmp_path / "data", "local")
    (root / ".execution.lock").symlink_to(tmp_path / "target")
    with pytest.raises(paths.UnsafePath):
        locks.execution_lock(root)
    assert not (tmp_path / "target").exists()
