"""arxiv collector — fetches recent papers in target categories."""

from datetime import UTC, datetime

import feedparser
import httpx
from prefect import flow, task
from sqlalchemy.orm import Session

from isbe.facts.db import make_session_factory
from isbe.topics.nowcasting.facts import Paper

ARXIV_QUERY_URL = (
    "https://export.arxiv.org/api/query"
    "?search_query=cat:cs.LG+AND+(abs:nowcasting+OR+abs:precipitation+OR+abs:radar)"
    "&sortBy=submittedDate&sortOrder=descending&max_results={max_results}"
)


def _parse_iso(s: str) -> datetime:
    # arxiv uses ISO with 'Z'
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def parse_atom_entry(entry: dict) -> Paper:
    """Parse one feedparser entry dict into a Paper ORM object (not yet attached to session)."""
    raw_id = entry["id"]  # "http://arxiv.org/abs/2604.12345v1"
    arxiv_id = raw_id.rsplit("/", 1)[-1].split("v")[0]
    authors = [a["name"] for a in entry.get("authors", [])]
    tags = entry.get("tags", [])
    primary = tags[0]["term"] if tags else "unknown"
    alt = next(
        (link["href"] for link in entry.get("links", []) if link.get("rel") == "alternate"),
        "",
    )
    return Paper(
        arxiv_id=arxiv_id,
        title=entry["title"].strip(),
        authors=authors,
        abstract=entry.get("summary", "").strip(),
        primary_category=primary,
        submitted_at=_parse_iso(entry["published"]),
        updated_at=_parse_iso(entry.get("updated", entry["published"])),
        pdf_uri=None,  # P1 不下载 PDF
        source_url=alt or raw_id,
    )


def upsert_papers(session: Session, papers: list[Paper]) -> int:
    """Insert papers that don't exist yet. Returns count of inserts."""
    n = 0
    for p in papers:
        existing = session.get(Paper, p.arxiv_id)
        if existing is None:
            session.add(p)
            n += 1
    session.commit()
    return n


@task
def fetch_arxiv_atom(max_results: int = 50) -> list[dict]:
    url = ARXIV_QUERY_URL.format(max_results=max_results)
    resp = httpx.get(url, timeout=30.0)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.text)
    return parsed.entries  # list of dicts


@flow(name="arxiv-collector")
def arxiv_collector(max_results: int = 50) -> int:
    entries = fetch_arxiv_atom(max_results)
    papers = [parse_atom_entry(e) for e in entries]
    Session = make_session_factory()
    with Session() as s:
        n = upsert_papers(s, papers)
    return n


if __name__ == "__main__":
    print(arxiv_collector(max_results=10))
