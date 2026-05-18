import shutil
from pathlib import Path

from isbe.topics.base import PendingMemoryDraft

PENDING = ".pending"
AUDIT_REJECTED = (".audit", "rejected")


def _safe_join(root: Path, *parts: str | Path) -> Path:
    """Resolve `root / parts...` and assert the result stays under `root`.

    Threat model: parts may come from LLM output (target_path of a memory
    draft). Without this guard, `../../../etc/passwd.md` would escape
    memory_root and let a prompt-injected response overwrite host files.
    """
    target = (root.joinpath(*parts)).resolve()
    root_resolved = root.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as e:
        raise ValueError(
            f"path {target} resolves outside memory_root {root_resolved}"
        ) from e
    return target


def write_pending(memory_root: Path, draft: PendingMemoryDraft) -> Path:
    target = _safe_join(memory_root, PENDING, draft.target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(draft.body, encoding="utf-8")
    return target


def list_pending(memory_root: Path) -> list[Path]:
    pending_root = memory_root / PENDING
    if not pending_root.exists():
        return []
    return sorted(p for p in pending_root.rglob("*.md") if p.is_file())


def _rel_under_pending(memory_root: Path, pending_path: Path) -> Path:
    """Resolve pending_path's relative location under .pending — reject if it
    escapes (symlink, manually-crafted, or hostile rename)."""
    pending_root = (memory_root / PENDING).resolve()
    try:
        return pending_path.resolve().relative_to(pending_root)
    except ValueError as e:
        raise ValueError(
            f"pending_path {pending_path} resolves outside memory_root/.pending"
        ) from e


def accept_pending(memory_root: Path, pending_path: Path) -> Path:
    rel = _rel_under_pending(memory_root, pending_path)
    target = _safe_join(memory_root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pending_path), str(target))
    return target


def reject_pending(memory_root: Path, pending_path: Path) -> Path:
    rel = _rel_under_pending(memory_root, pending_path)
    target = _safe_join(memory_root, *AUDIT_REJECTED, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pending_path), str(target))
    return target
