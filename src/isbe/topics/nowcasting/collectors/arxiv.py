"""arxiv collector — fetches recent papers in target categories."""

import io
import os
import time
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import feedparser
import httpx
from minio import Minio
from prefect import flow, task
from sqlalchemy import select
from sqlalchemy.orm import Session

from isbe.facts.db import make_session_factory
from isbe.observability.runs import topic_run
from isbe.topics.nowcasting.facts import Paper

PAPERS_BUCKET = "isbe-papers"
PAPERS_LOCAL_MIRROR_DEFAULT = Path("papers")
ARXIV_PDF_RATE_LIMIT_S = 3.0  # arXiv ToS: 1 request per 3s

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
    with topic_run("nowcasting", "arxiv-collector") as run:
        entries = fetch_arxiv_atom(max_results)
        papers = [parse_atom_entry(e) for e in entries]
        Session = make_session_factory()
        with Session() as s:
            n = upsert_papers(s, papers)
        run.payload["fetched"] = len(entries)
        run.payload["new_papers"] = n
        return n


@lru_cache(maxsize=1)
def _get_minio_client() -> Minio:
    return Minio(
        os.getenv("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.getenv("MINIO_ROOT_USER", "isbe"),
        secret_key=os.getenv("MINIO_ROOT_PASSWORD", "changeme123"),
        secure=False,
    )


def _ensure_papers_bucket(client: Minio) -> None:
    if not client.bucket_exists(PAPERS_BUCKET):
        client.make_bucket(PAPERS_BUCKET)


def fetch_pdf_bytes(arxiv_id: str) -> bytes:
    """GET https://arxiv.org/pdf/<arxiv_id> → PDF bytes."""
    resp = httpx.get(
        f"https://arxiv.org/pdf/{arxiv_id}",
        follow_redirects=True,
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.content


def store_pdf(arxiv_id: str, body: bytes) -> str:
    """Upload PDF to MinIO + write local mirror. Returns body_uri."""
    client = _get_minio_client()
    _ensure_papers_bucket(client)
    object_name = f"{arxiv_id}.pdf"
    client.put_object(
        PAPERS_BUCKET,
        object_name,
        io.BytesIO(body),
        length=len(body),
        content_type="application/pdf",
    )
    mirror_root = Path(os.getenv("ISBE_PAPERS_MIRROR", str(PAPERS_LOCAL_MIRROR_DEFAULT)))
    local_path = mirror_root / object_name
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(body)
    return f"minio://{PAPERS_BUCKET}/{object_name}"


@flow(name="arxiv-download-pdfs")
def arxiv_download_pdfs(limit: int = 10) -> int:
    """Download PDFs for papers where pdf_uri IS NULL.

    Throttled to 1 request / 3s per arXiv Terms of Service. Returns count downloaded.
    """
    with topic_run("nowcasting", "arxiv-download-pdfs") as run:
        Session = make_session_factory()
        n = 0
        skipped = 0
        with Session() as s:
            targets = list(
                s.scalars(select(Paper).where(Paper.pdf_uri.is_(None)).limit(limit)).all()
            )
            for p in targets:
                try:
                    body = fetch_pdf_bytes(p.arxiv_id)
                    p.pdf_uri = store_pdf(p.arxiv_id, body)
                    s.add(p)
                    s.commit()
                    n += 1
                except httpx.HTTPError as e:
                    print(f"[arxiv-pdfs] skip {p.arxiv_id}: {e}")
                    skipped += 1
                time.sleep(ARXIV_PDF_RATE_LIMIT_S)
        run.payload["downloaded"] = n
        run.payload["skipped"] = skipped
        run.payload["limit"] = limit
        return n


if __name__ == "__main__":
    print(arxiv_collector(max_results=10))
