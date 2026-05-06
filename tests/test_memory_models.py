from datetime import date

import pytest
from pydantic import ValidationError

from isbe.memory.models import MemoryFrontmatter, MemorySource, MemoryType


def test_minimal_valid_frontmatter():
    fm = MemoryFrontmatter(
        name="digest_style",
        description="用户偏好的日报风格",
        type=MemoryType.feedback,
        created=date(2026, 5, 6),
        updated=date(2026, 5, 6),
        source=MemorySource.user_edited,
    )
    assert fm.revision == 1
    assert fm.tags == []
    assert fm.supersedes == []


def test_description_max_length():
    with pytest.raises(ValidationError):
        MemoryFrontmatter(
            name="x",
            description="a" * 151,
            type=MemoryType.feedback,
            created=date(2026, 5, 6),
            updated=date(2026, 5, 6),
            source=MemorySource.user_edited,
        )


def test_type_must_be_enum():
    with pytest.raises(ValidationError):
        MemoryFrontmatter(
            name="x",
            description="d",
            type="invented_type",
            created=date(2026, 5, 6),
            updated=date(2026, 5, 6),
            source=MemorySource.user_edited,
        )


def test_revision_increments_via_validator():
    fm = MemoryFrontmatter(
        name="x",
        description="d",
        type=MemoryType.user,
        created=date(2026, 5, 6),
        updated=date(2026, 5, 6),
        source=MemorySource.agent_inferred,
        revision=5,
    )
    assert fm.revision == 5
