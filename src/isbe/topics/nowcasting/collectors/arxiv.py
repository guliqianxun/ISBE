"""nowcasting-specific arxiv wiring.

Most of the arxiv work is now generic and lives in `isbe.topics._shared.arxiv`.
This module:
  - re-exports the generic helpers under their old import path (test compat)
  - keeps the PDF download flow (still nowcasting-bound; other topics don't
    download PDFs in the MVP)
  - exposes a thin `arxiv_collector()` wrapper that drives the generic flow
    with topic_id='nowcasting'
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
    parse_atom_entry,  # noqa: F401  — used by tests
    upsert_papers,  # noqa: F401  — used by tests
)
from isbe.topics.nowcasting.facts import Paper

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


def fetch_pdf_bytes(arxiv_id: str, *, max_retries: int = 2, timeout: float = 180.0) -> bytes:
    """Fetch one PDF, trying configured mirrors with retries.

    Raises the LAST exception if all mirrors+retries fail.
    """
    last_exc: Exception | None = None
    for base in _arxiv_pdf_base_urls():
        url = f"{base}/pdf/{arxiv_id}"
        for attempt in range(max_retries + 1):
            try:
                resp = httpx.get(url, follow_redirects=True, timeout=timeout)
                resp.raise_for_status()
                return resp.content
            except (httpx.TimeoutException, httpx.HTTPError) as e:
                last_exc = e
                if attempt < max_retries:
                    time.sleep(2 * (attempt + 1))  # 2s, 4s backoff
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


@flow(name="arxiv-download-pdfs")
def arxiv_download_pdfs(limit: int = 10, period_label: str | None = None) -> int:
    """Download PDFs for papers where pdf_uri IS NULL (rate-limited 1/3s).

    Currently nowcasting-bound for organization (papers/<topic>/<period>/).
    New topics can opt in by adding their own download flow if needed.
    """
    import sys

    period = period_label or _current_iso_week()
    with topic_run(TOPIC_ID, "arxiv-download-pdfs") as run:
        Session = make_session_factory()
        n = 0
        skipped = 0
        with Session() as s:
            targets = list(
                s.scalars(select(Paper).where(Paper.pdf_uri.is_(None)).limit(limit)).all()
            )
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
                    p.pdf_uri = store_pdf(p.arxiv_id, body, period_label=period)
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
        return n


if __name__ == "__main__":
    print(arxiv_collector(max_results=10))
