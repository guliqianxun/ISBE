import shutil
from datetime import date
from pathlib import Path

import frontmatter

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


def _uniquify(path: Path) -> Path:
    """Return `path`, or `stem-2.md`, `stem-3.md`, ... if it already exists.

    Two DRAFT lines in one digest run may target the same path; overwriting
    would silently drop all but the last draft.
    """
    if not path.exists():
        return path
    for i in range(2, 100):
        candidate = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"too many pending drafts for {path}")


def write_pending(memory_root: Path, draft: PendingMemoryDraft) -> Path:
    target = _safe_join(memory_root, PENDING, draft.target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target = _uniquify(target)
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


def merge_memory_texts(existing: str, draft: str, today: date | None = None) -> str:
    """Merge an accepted draft into an existing memory file's text.

    Keeps the existing frontmatter, bumps `revision`, stamps `updated`, and
    appends the draft body (frontmatter stripped) under a dated heading. The
    pre-merge behavior replaced the whole file with the draft, destroying the
    accumulated theses and resetting the revision counter.
    """
    today = today or date.today()
    existing_post = frontmatter.loads(existing)
    draft_post = frontmatter.loads(draft)
    existing_post.metadata["revision"] = int(existing_post.metadata.get("revision", 1)) + 1
    existing_post.metadata["updated"] = today.isoformat()
    draft_body = draft_post.content.strip()
    existing_post.content = (
        f"{existing_post.content.rstrip()}\n\n## 合入 {today.isoformat()}（digest 草稿）\n\n"
        f"{draft_body}\n"
    )
    return frontmatter.dumps(existing_post)


def accept_pending(memory_root: Path, pending_path: Path) -> Path:
    """Accept a pending draft: move it into place, or merge if the target exists.

    Note: a draft uniquified by write_pending (e.g. `x-2.md`) accepts to its
    own literal path; it is not folded back into `x.md` automatically — the
    suffix is visible to the reviewer, who can merge by hand if desired.
    """
    rel = _rel_under_pending(memory_root, pending_path)
    target = _safe_join(memory_root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        merged = merge_memory_texts(
            target.read_text(encoding="utf-8"),
            pending_path.read_text(encoding="utf-8"),
        )
        target.write_text(merged, encoding="utf-8")
        pending_path.unlink()
    else:
        shutil.move(str(pending_path), str(target))
    return target


def reject_pending(memory_root: Path, pending_path: Path) -> Path:
    rel = _rel_under_pending(memory_root, pending_path)
    target = _safe_join(memory_root, *AUDIT_REJECTED, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pending_path), str(target))
    return target
