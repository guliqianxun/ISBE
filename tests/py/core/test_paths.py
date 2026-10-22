import os
import stat
from pathlib import Path

import pytest

from isbe.core import paths

OWNER = "0123456789abcdef0123456789abcdef"


def test_workspace_layout_and_privacy(tmp_path: Path) -> None:
    root = tmp_path / "data"
    assert paths.workspace(root, "local") == root.resolve()
    assert (root / "topics").is_dir()
    owner = paths.workspace(root, OWNER)
    assert owner == (root / "workspaces" / OWNER).resolve()
    for directory in (root / "workspaces", owner, owner / "topics"):
        assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700


def test_workspace_links_and_nested_report_links_are_rejected(tmp_path: Path) -> None:
    data, outside = tmp_path / "data", tmp_path / "outside"
    data.mkdir()
    outside.mkdir()
    (data / "workspaces").symlink_to(outside)
    with pytest.raises(paths.UnsafePath):
        paths.workspace(data, OWNER)
    assert not (outside / OWNER).exists()
    (data / "workspaces").unlink()
    owner = paths.workspace(data, OWNER)
    reports = owner / "reports"
    reports.mkdir()
    (reports / "papers").symlink_to(outside)
    with pytest.raises(paths.UnsafePath):
        paths.check_tree(reports)
    (reports / "papers").unlink()
    (reports / "papers").mkdir()
    (reports / "papers" / "deep.md").symlink_to(outside / "x")
    with pytest.raises(paths.UnsafePath):
        paths.check_tree(reports)
    for owner_name in ["../local", "LOCAL", "abc", "0123456789ABCDEF0123456789ABCDEF"]:
        with pytest.raises(paths.UnsafePath):
            paths.workspace(data, owner_name)


def test_linked_root_ancestors_and_well_known_entries_are_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "alias").symlink_to(real)
    with pytest.raises(paths.UnsafePath):
        paths.workspace(tmp_path / "alias" / "data", "local")
    data = tmp_path / "data"
    data.mkdir()
    (data / "isbe.db").symlink_to(tmp_path / "elsewhere.db")
    with pytest.raises(paths.UnsafePath):
        paths.workspace(data, "local")
    (data / "isbe.db").unlink()
    (data / "topics").write_text("not a directory")
    with pytest.raises(paths.UnsafePath, match="must be a directory"):
        paths.workspace(data, "local")


def test_atomic_write_replaces_with_private_mode_and_unique_temporaries(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "file.json"
    paths.atomic_write(target, "first")
    assert target.read_text() == "first"
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    paths.atomic_write(target, b"second", mode=0o644)
    assert target.read_bytes() == b"second"
    assert stat.S_IMODE(os.stat(target).st_mode) & 0o077 in (0o044, 0o004, 0o040, 0)
    assert [p.name for p in target.parent.iterdir()] == ["file.json"]


def test_atomic_write_create_new_never_replaces(tmp_path: Path) -> None:
    target = tmp_path / "memo.md"
    paths.atomic_write(target, "original", create_new=True)
    with pytest.raises(FileExistsError):
        paths.atomic_write(target, "clobber", create_new=True)
    assert target.read_text() == "original"
    assert [p.name for p in tmp_path.iterdir()] == ["memo.md"]


def test_staged_write_commits_only_when_asked(tmp_path: Path) -> None:
    target = tmp_path / "topic.yaml"
    target.write_text("revision 1")
    with paths.staged_write(target, "revision 2") as staged:
        assert staged.temp.read_text() == "revision 2"
        assert target.read_text() == "revision 1", "nothing visible before commit"
        staged.commit()
    assert target.read_text() == "revision 2"
    with paths.staged_write(target, "stale revision"):
        pass  # the caller's revision check failed: no commit
    with pytest.raises(RuntimeError):
        with paths.staged_write(target, "failed check") as staged:
            raise RuntimeError("revision changed")
    assert target.read_text() == "revision 2"
    with paths.staged_write(tmp_path / "new.yaml", "x", create_new=True) as staged:
        staged.commit()
    with paths.staged_write(tmp_path / "new.yaml", "y", create_new=True) as staged:
        with pytest.raises(FileExistsError):
            staged.commit()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["new.yaml", "topic.yaml"]
    assert (tmp_path / "new.yaml").read_text() == "x"


def test_atomic_write_refuses_symlink_targets(tmp_path: Path) -> None:
    (tmp_path / "secret").write_text("keep")
    (tmp_path / "link").symlink_to(tmp_path / "secret")
    with pytest.raises(paths.UnsafePath):
        paths.atomic_write(tmp_path / "link", "overwrite")
    assert (tmp_path / "secret").read_text() == "keep"
