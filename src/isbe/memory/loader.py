from dataclasses import dataclass
from pathlib import Path

import frontmatter

from isbe.memory.models import MemoryFrontmatter

EXCLUDED_DIRS = {".pending", ".audit"}


@dataclass(frozen=True)
class MemoryFile:
    path: Path
    frontmatter: MemoryFrontmatter
    body: str


def load_file(path: Path) -> MemoryFile:
    post = frontmatter.load(path)
    fm = MemoryFrontmatter.model_validate(post.metadata)
    return MemoryFile(path=path, frontmatter=fm, body=post.content)


def load_index(memory_root: Path) -> list[MemoryFile]:
    """Load all memory files under memory_root, skipping .pending and .audit."""
    out: list[MemoryFile] = []
    for md in memory_root.rglob("*.md"):
        if md.name == "MEMORY.md":
            continue
        if any(part in EXCLUDED_DIRS for part in md.parts):
            continue
        out.append(load_file(md))
    return out
