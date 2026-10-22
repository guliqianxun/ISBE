from collections.abc import Iterator
from pathlib import Path

import pytest
from research_helpers import Fixture


@pytest.fixture
def fixture(tmp_path: Path) -> Iterator[Fixture]:
    yield Fixture(tmp_path / "data")
