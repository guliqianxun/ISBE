import shutil
from pathlib import Path

from isbe.topics.base import PendingMemoryDraft

PENDING = ".pending"
AUDIT_REJECTED = (".audit", "rejected")


def write_pending(memory_root: Path, draft: PendingMemoryDraft) -> Path:
    target = memory_root / PENDING / draft.target_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(draft.body, encoding="utf-8")
    return target


def list_pending(memory_root: Path) -> list[Path]:
    pending_root = memory_root / PENDING
    if not pending_root.exists():
        return []
    return sorted(p for p in pending_root.rglob("*.md") if p.is_file())


def accept_pending(memory_root: Path, pending_path: Path) -> Path:
    rel = pending_path.relative_to(memory_root / PENDING)
    target = memory_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pending_path), str(target))
    return target


def reject_pending(memory_root: Path, pending_path: Path) -> Path:
    rel = pending_path.relative_to(memory_root / PENDING)
    target = memory_root / AUDIT_REJECTED[0] / AUDIT_REJECTED[1] / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pending_path), str(target))
    return target
