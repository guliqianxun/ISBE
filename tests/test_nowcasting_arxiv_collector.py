from datetime import UTC, datetime
from unittest.mock import MagicMock

from isbe.topics.nowcasting.collectors.arxiv import (
    parse_atom_entry,
    upsert_papers,
)

SAMPLE_ATOM_ENTRY = {
    "id": "http://arxiv.org/abs/2604.12345v1",
    "title": "Diffusion-based Nowcasting via Radar Conditioning",
    "summary": "We present a new method...",
    "authors": [{"name": "Alice"}, {"name": "Bob"}],
    "published": "2026-04-30T12:00:00Z",
    "updated": "2026-05-01T08:00:00Z",
    "tags": [{"term": "cs.LG"}, {"term": "physics.ao-ph"}],
    "links": [{"rel": "alternate", "href": "https://arxiv.org/abs/2604.12345"}],
}


def test_parse_atom_entry_extracts_fields():
    paper = parse_atom_entry(SAMPLE_ATOM_ENTRY)
    assert paper.arxiv_id == "2604.12345"
    assert paper.authors == ["Alice", "Bob"]
    assert paper.primary_category == "cs.LG"
    assert paper.submitted_at == datetime(2026, 4, 30, 12, 0, tzinfo=UTC)


def test_upsert_papers_inserts_new_and_skips_dup():
    session = MagicMock()
    # Simulate "Alice" paper already exists for second call
    session.get.side_effect = [None, MagicMock()]  # first: None; second: existing
    paper1 = parse_atom_entry(SAMPLE_ATOM_ENTRY)
    paper2 = parse_atom_entry(SAMPLE_ATOM_ENTRY)
    n_new = upsert_papers(session, [paper1, paper2])
    assert n_new == 1
