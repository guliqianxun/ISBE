import pytest

pytestmark = pytest.mark.integration

from sqlalchemy import inspect

from isbe.facts.db import make_engine


def test_artifacts_and_topic_runs_tables_exist():
    """After alembic upgrade head, both common tables exist."""
    eng = make_engine()
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    assert "artifacts" in tables
    assert "topic_runs" in tables
