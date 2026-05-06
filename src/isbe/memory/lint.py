from dataclasses import dataclass
from pathlib import Path

import frontmatter
from pydantic import ValidationError

from isbe.memory.models import MemoryFrontmatter

BODY_SIZE_LIMIT_BYTES = 4096


@dataclass(frozen=True)
class LintError:
    path: Path
    message: str


def lint_file(path: Path) -> list[LintError]:
    errors: list[LintError] = []
    post = frontmatter.load(path)

    try:
        fm = MemoryFrontmatter.model_validate(post.metadata)
    except ValidationError as e:
        errors.append(LintError(path, f"frontmatter invalid: {e.errors()[0]['msg']}"))
        return errors

    if fm.name != path.stem:
        errors.append(LintError(path, f"name '{fm.name}' must match file stem '{path.stem}'"))

    body_bytes = post.content.encode("utf-8")
    if len(body_bytes) > BODY_SIZE_LIMIT_BYTES:
        errors.append(
            LintError(path, f"body size {len(body_bytes)} bytes exceeds {BODY_SIZE_LIMIT_BYTES}")
        )

    return errors
