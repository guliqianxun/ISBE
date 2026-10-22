"""File memory under `<data>/memory/`: markdown notes with YAML frontmatter.
The digest reads notes and proposes drafts into `.pending/`; review and merge,
the only way into the live tree, are manual (`isbe review` is not built yet).
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from isbe.core import paths
from isbe.topics import decode, yamlio
from isbe.topics.decode import U32
from isbe.topics.model import debug

_KINDS = {"topics": "topic", "reading": "reading", "feedback": "feedback", "user": "user", "reference": "reference"}
_MAX_SUFFIX = 1000


@dataclass(frozen=True)
class Note:
    name: str
    kind: str
    revision: int
    body: str


class _Front(BaseModel):
    # Other frontmatter keys (description, dates, source) are kept in the file but not read.
    model_config = ConfigDict(extra="ignore")

    name: str
    kind: str = Field(alias="type")
    revision: Annotated[int, U32] = 1


def read_note(path: Path) -> Note:
    """Parse one note; raises `ValueError` (or `OSError`) when it is unreadable."""
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    rest = text.removeprefix("---\n") if text.startswith("---\n") else None
    if rest is None:
        raise ValueError("no --- frontmatter")
    front, sep, body = rest.partition("\n---\n")
    if not sep:
        if not rest.endswith("\n---"):
            raise ValueError("no --- frontmatter")
        front, body = rest.removesuffix("\n---"), ""
    f = decode.decode(front, _Front)
    return Note(name=f.name, kind=f.kind, revision=f.revision, body=body.strip())


def _walk(dir: Path, out: list[Path]) -> None:
    """Every `.md` under `dir`, skipping dot-entries (`.pending`, `.audit`)."""
    with os.scandir(dir) as entries:
        for entry in entries:
            if entry.name.startswith("."):
                continue
            path = Path(entry.path)
            if entry.is_dir():
                _walk(path, out)
            elif path.suffix == ".md":
                out.append(path)


def load(root: Path, topic: str) -> tuple[list[Note], list[str]]:
    """Notes the digest of `topic` reads: that topic's own (`name` is the topic
    id or starts with `<id>.`), plus every `feedback` and `user` note.
    Unreadable notes are skipped and reported in the second value."""
    files: list[Path] = []
    if root.exists():
        _walk(root, files)
    files.sort(key=lambda p: p.parts)
    notes: list[Note] = []
    skipped: list[str] = []
    for path in files:
        try:
            note = read_note(path)
        except (OSError, ValueError) as e:
            skipped.append(f"{path}: {e}")
            continue
        if note.kind == "topic":
            ours = note.name == topic or note.name.startswith(f"{topic}.")
        else:
            ours = note.kind in ("feedback", "user")
        if ours:
            notes.append(note)
    return notes, skipped


def pending(root: Path) -> list[str]:
    """The drafts waiting for review, as `/`-separated paths under `.pending/` (`topics/x.md`)."""
    dir = root / ".pending"
    files: list[Path] = []
    if dir.is_dir():
        _walk(dir, files)
    return sorted(p.relative_to(dir).as_posix() for p in files)


def _frontmatter(stem: str, origin: str, kind: str, date: str) -> str:
    # Every value is quoted, because the name comes from
    # model output and the origin carries colons (`digest draft: t 2026-W42`).
    fields = {"name": stem, "description": origin, "type": kind, "created": date, "updated": date, "source": "agent-inferred"}
    lines = [f"{key}: {yamlio.quote(value)}" for key, value in fields.items()]
    return "---\n" + "\n".join(lines) + "\nrevision: 1\n---\n"


def write_draft(root: Path, target: str, content: str, origin: str, date: str) -> Path:
    """Write an LLM-proposed note to `.pending/<target>` for human review; never
    into the live tree. `target` comes from model output, so it must be a plain
    relative `<type dir>/....md` path; a second draft for the same target gets
    a `-2`, `-3`... suffix instead of overwriting the first, and an identical
    one (a re-run digest) is not written again."""
    if "\\" in target or target.startswith("/") or target.split("/")[0] == "." or ".." in target.split("/"):
        raise ValueError(f"unsafe draft path {debug(target)}")
    kind = _KINDS.get(target.split("/")[0])
    if kind is None:
        raise ValueError(f"draft path {debug(target)} must start with topics/, reading/, feedback/, user/ or reference/")
    if not target.endswith(".md"):
        raise ValueError(f"draft path {debug(target)} must end in .md")
    stem = target.removesuffix(".md").rsplit("/", 1)[-1]
    body = _frontmatter(stem, origin, kind, date) + content.strip() + "\n"
    path = root / ".pending" / target
    for i in range(2, _MAX_SUFFIX):
        try:
            if path.read_text(encoding="utf-8") == body:
                return path
        except FileNotFoundError:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                paths.atomic_write(path, body, create_new=True)
                return path
            except FileExistsError:
                if path.read_text(encoding="utf-8", errors="replace") == body:
                    return path
        except (IsADirectoryError, UnicodeDecodeError):
            pass  # taken by something that is not our draft; never overwrite it
        path = path.with_name(f"{stem}-{i}.md")
    raise ValueError(f"too many drafts for {debug(target)}")
