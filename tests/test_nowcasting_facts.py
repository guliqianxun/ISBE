import pytest

pytestmark = pytest.mark.integration

from datetime import UTC, datetime

from sqlalchemy import inspect, select

from isbe.facts.db import make_engine, make_session_factory
from isbe.topics.nowcasting.facts import Paper


def test_tables_created():
    insp = inspect(make_engine())
    tables = set(insp.get_table_names())
    assert {"papers", "repos", "events"} <= tables


def test_paper_roundtrip():
    Session = make_session_factory()
    with Session() as s:
        p = Paper(
            arxiv_id="2604.12345",
            title="Test paper",
            authors=["Alice", "Bob"],
            abstract="abstract body",
            primary_category="cs.LG",
            submitted_at=datetime(2026, 5, 1, tzinfo=UTC),
            updated_at=datetime(2026, 5, 1, tzinfo=UTC),
            pdf_uri="minio://papers/2604.12345.pdf",
            source_url="https://arxiv.org/abs/2604.12345",
        )
        s.add(p)
        s.commit()
        got = s.scalar(select(Paper).where(Paper.arxiv_id == "2604.12345"))
        assert got is not None
        assert got.authors == ["Alice", "Bob"]
        s.delete(got)
        s.commit()
