"""arxiv PDF download — topic-agnostic.

The historical home of this module was nowcasting-specific; it now serves
any topic with an `arxiv:` block in `topic.yaml`. PDFs are organized on
disk as `papers/<topic_id>/<period>/<arxiv_id>.pdf`. A paper is stored
under the topic that *first* downloads it (paper rows are shared across
topics; `pdf_uri` is single-valued).
"""

import io
import os
import time
from datetime import date
from functools import lru_cache
from pathlib import Path

import httpx
from minio import Minio
from prefect import flow
from sqlalchemy import select

from isbe.facts.db import make_session_factory
from isbe.observability.runs import topic_run
from isbe.topics._shared.arxiv import (
    arxiv_collector as _generic_arxiv_collector,
)
from isbe.topics._shared.arxiv import (
    fetch_arxiv_atom,  # noqa: F401  — re-export for parity
    papers_keyword_filter,
    parse_atom_entry,  # noqa: F401  — used by tests
    upsert_papers,  # noqa: F401  — used by tests
)
from isbe.topics._shared.semantic_scholar import open_access_pdf_url
from isbe.topics.nowcasting.facts import Paper
from isbe.topics.registry import default_topics_root, load_topic_config

TOPIC_ID = "nowcasting"
PAPERS_LOCAL_MIRROR_DEFAULT = Path("papers")
ARXIV_PDF_RATE_LIMIT_S = 3.0  # arXiv ToS: 1 request per 3s


def _papers_bucket(topic_id: str) -> str:
    return f"papers-{topic_id}"


def _current_iso_week() -> str:
    year, week, _ = date.today().isocalendar()
    return f"{year}-W{week:02d}"


def arxiv_collector(max_results: int = 50) -> int:
    """Backwards-compat wrapper: runs the generic collector for nowcasting."""
    return _generic_arxiv_collector(topic_id=TOPIC_ID, max_results=max_results)


@lru_cache(maxsize=1)
def _get_minio_client() -> Minio:
    return Minio(
        os.getenv("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.getenv("MINIO_ROOT_USER", "isbe"),
        secret_key=os.getenv("MINIO_ROOT_PASSWORD", "changeme123"),
        secure=False,
    )


def _ensure_papers_bucket(client: Minio, bucket: str) -> None:
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def _arxiv_pdf_base_urls() -> list[str]:
    """Candidate base URLs in order of preference.

    Override via ARXIV_PDF_BASE_URL (single) or ARXIV_PDF_MIRRORS (comma-separated list).
    Default puts export.arxiv.org FIRST — empirically more reachable from CN/Asia
    routes than arxiv.org, which often times out entirely. arxiv.org kept as
    second fallback for non-CN deploys where it might be faster.
    """
    explicit = os.getenv("ARXIV_PDF_BASE_URL", "").strip()
    if explicit:
        return [explicit.rstrip("/")]
    mirrors = os.getenv("ARXIV_PDF_MIRRORS", "").strip()
    if mirrors:
        return [m.strip().rstrip("/") for m in mirrors.split(",") if m.strip()]
    return ["https://export.arxiv.org", "https://arxiv.org"]


def fetch_pdf_bytes(arxiv_id: str, *, max_retries: int = 2, timeout: float | None = None) -> bytes:
    """Fetch one PDF: arxiv mirrors first, Semantic Scholar OA link as fallback.

    Per-attempt read timeout defaults to 180s, tunable via ARXIV_PDF_TIMEOUT_S
    (CN routes often stall — a lower value fails over to the next mirror
    faster). Connect timeout is a tight 10s regardless.

    When every mirror+retry fails, S2's `openAccessPdf` direct link is tried —
    it usually sits on a different CDN than arxiv.org. The response must start
    with %PDF- (OA links occasionally point at publisher landing pages).
    Raises the LAST exception if everything fails.
    """
    if timeout is None:
        timeout = float(os.getenv("ARXIV_PDF_TIMEOUT_S", "180"))
    http_timeout = httpx.Timeout(timeout, connect=10.0)
    last_exc: Exception | None = None
    for base in _arxiv_pdf_base_urls():
        url = f"{base}/pdf/{arxiv_id}"
        for attempt in range(max_retries + 1):
            try:
                resp = httpx.get(url, follow_redirects=True, timeout=http_timeout)
                resp.raise_for_status()
                return resp.content
            except (httpx.TimeoutException, httpx.HTTPError) as e:
                last_exc = e
                if attempt < max_retries:
                    time.sleep(2 * (attempt + 1))  # 2s, 4s backoff

    s2_url = open_access_pdf_url(arxiv_id)
    if s2_url:
        print(f"[arxiv-pdfs] {arxiv_id}: mirrors failed, trying S2 OA link", flush=True)
        try:
            resp = httpx.get(s2_url, follow_redirects=True, timeout=http_timeout)
            resp.raise_for_status()
            if resp.content[:5] == b"%PDF-":
                return resp.content
            print(f"[arxiv-pdfs] {arxiv_id}: S2 link is not a PDF, giving up", flush=True)
        except (httpx.TimeoutException, httpx.HTTPError) as e:
            last_exc = e
    assert last_exc is not None
    raise last_exc


def store_pdf(
    arxiv_id: str,
    body: bytes,
    *,
    topic_id: str = TOPIC_ID,
    period_label: str,
) -> str:
    bucket = _papers_bucket(topic_id)
    object_name = f"{period_label}/{arxiv_id}.pdf"
    client = _get_minio_client()
    _ensure_papers_bucket(client, bucket)
    client.put_object(
        bucket,
        object_name,
        io.BytesIO(body),
        length=len(body),
        content_type="application/pdf",
    )
    mirror_root = Path(os.getenv("ISBE_PAPERS_MIRROR", str(PAPERS_LOCAL_MIRROR_DEFAULT)))
    local_path = mirror_root / topic_id / period_label / f"{arxiv_id}.pdf"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(body)
    return f"minio://{bucket}/{object_name}"


def _topic_keyword_filter(topic_id: str):
    """SQLAlchemy filter scoping papers to the topic's `arxiv:include_keywords`."""
    cfg = load_topic_config(default_topics_root(), topic_id)
    keywords = (cfg.get("arxiv") or {}).get("include_keywords", [])
    return papers_keyword_filter(keywords)


@flow(name="arxiv-download-pdfs")
def arxiv_download_pdfs(
    topic_id: str = TOPIC_ID,
    limit: int = 0,
    period_label: str | None = None,
) -> int:
    """Download PDFs of papers matching `topic_id`'s keywords (rate-limited 1/3s).

    `limit=0` means no cap; PDFs land in `papers/<topic_id>/<period>/`.
    Papers whose `pdf_uri` is already set are skipped (a paper can only have
    one download location; first topic to claim it wins).
    """
    import sys

    period = period_label or _current_iso_week()
    with topic_run(topic_id, "arxiv-download-pdfs") as run:
        Session = make_session_factory()
        n = 0
        skipped = 0
        with Session() as s:
            stmt = select(Paper).where(Paper.pdf_uri.is_(None))
            kw_filter = _topic_keyword_filter(topic_id)
            if kw_filter is not None:
                stmt = stmt.where(kw_filter)
            if limit and limit > 0:
                stmt = stmt.limit(limit)
            targets = list(s.scalars(stmt).all())
            total = len(targets)
            print(
                f"[arxiv-pdfs] starting: {total} target paper(s), "
                f"period={period}, mirrors={_arxiv_pdf_base_urls()}",
                flush=True,
            )
            for idx, p in enumerate(targets, 1):
                t0 = time.time()
                print(f"[arxiv-pdfs] ({idx}/{total}) -> {p.arxiv_id} fetching...", flush=True)
                try:
                    body = fetch_pdf_bytes(p.arxiv_id)
                    p.pdf_uri = store_pdf(
                        p.arxiv_id, body, topic_id=topic_id, period_label=period
                    )
                    s.add(p)
                    s.commit()
                    n += 1
                    elapsed = time.time() - t0
                    print(
                        f"[arxiv-pdfs] ({idx}/{total}) OK {p.arxiv_id} "
                        f"{len(body) / 1024:.0f}KB in {elapsed:.1f}s",
                        flush=True,
                    )
                except httpx.HTTPError as e:
                    elapsed = time.time() - t0
                    print(
                        f"[arxiv-pdfs] ({idx}/{total}) SKIP {p.arxiv_id} "
                        f"after {elapsed:.1f}s: {e}",
                        file=sys.stderr,
                        flush=True,
                    )
                    skipped += 1
                time.sleep(ARXIV_PDF_RATE_LIMIT_S)
        run.payload["downloaded"] = n
        run.payload["skipped"] = skipped
        run.payload["limit"] = limit
        run.payload["period_label"] = period
        run.payload["topic_id"] = topic_id
        return n


if __name__ == "__main__":
    print(arxiv_collector(max_results=10))
