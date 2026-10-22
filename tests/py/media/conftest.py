from pathlib import Path

import pytest


@pytest.fixture
def media_data(tmp_path: Path) -> Path:
    """A bare data directory (no `reports/` yet)."""
    root = tmp_path / "data"
    root.mkdir()
    return root
