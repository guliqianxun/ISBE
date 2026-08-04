"""metrail-enrich flow — extract full text from already-downloaded arXiv PDFs.

Runs after arxiv_download_pdfs and before the weekly digester: papers with a
`pdf_uri` but no `fulltext_uri` are read from the LOCAL papers mirror (never
MinIO), pushed through the LAN metrail-web service, and the resulting corpus
markdown is written next to the PDF as `<arxiv_id>.metrail.md`. The digester
then folds excerpts into its prompt (see _shared/digester.py).

Per-paper failures (service down, timeout, missing local PDF) are skipped and
recorded in the run payload; `fulltext_uri` stays NULL so the paper is retried
on the next run. With METRAIL_API_URL unset (and no per-topic base_url), the
flow is a no-op — schedule-safe on hosts without the service.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx
from prefect import flow
from sqlalchemy import select

from isbe.facts.db import make_session_factory
from isbe.observability.runs import topic_run
from isbe.topics._shared.arxiv import papers_keyword_filter
from isbe.topics._shared.metrail import (
    MetrailError,
    download_asset,
    fetch_corpus_markdown,
    fetch_figure_atoms,
    metrail_base_url,
    submit_pdf,
    wait_until_done,
)
from isbe.topics.nowcasting.facts import Paper
from isbe.topics.registry import default_topics_root, load_topic_config_typed

_MIRROR_ENV = "ISBE_PAPERS_MIRROR"

# Framework-figure caption heuristic (same spirit as scripts/extract_assets.py)
_FRAMEWORK_RE = re.compile(
    r"architecture|framework|overview|pipeline|network|schematic|workflow|"
    r"架构|框架|总体|流程",
    re.IGNORECASE,
)
# Email-friendly cap: figures above this are skipped (data-URI inlining)
_MAX_FIGURE_BYTES = 500_000


def _mirror_root() -> Path:
    return Path(os.getenv(_MIRROR_ENV, "papers"))


def _local_pdf_path(pdf_uri: str) -> Path | None:
    """Map `minio://papers-<topic>/<period>/<id>.pdf` to the local mirror copy.

    The mirror is written unconditionally by store_pdf(); the bucket's topic
    segment may differ from the current topic (a paper is stored under the
    topic that first downloaded it).
    """
    if not pdf_uri.startswith("minio://"):
        return None
    bucket, _, object_name = pdf_uri[len("minio://") :].partition("/")
    if not bucket.startswith("papers-") or not object_name:
        return None
    owning_topic = bucket[len("papers-") :]
    return _mirror_root() / owning_topic / object_name


def _fetch_pdf_from_minio(pdf_uri: str, local_pdf: Path) -> bool:
    """Pull a PDF from MinIO into the local mirror.

    LAN fallback for PDFs downloaded by another host (e.g. a proxy-equipped
    dev machine writing straight to server MinIO) or predating the
    unconditional mirror write. Best-effort: False on any failure.
    """
    try:
        from minio import Minio  # lazy: not needed on the happy path

        bucket, _, object_name = pdf_uri[len("minio://") :].partition("/")
        if not bucket or not object_name:
            return False
        client = Minio(
            os.getenv("MINIO_ENDPOINT", "localhost:9000"),
            access_key=os.getenv("MINIO_ROOT_USER", "isbe"),
            secret_key=os.getenv("MINIO_ROOT_PASSWORD", "changeme123"),
            secure=False,
        )
        resp = client.get_object(bucket, object_name)
        try:
            data = resp.read()
        finally:
            resp.close()
            resp.release_conn()
        if not data.startswith(b"%PDF-"):
            return False
        local_pdf.parent.mkdir(parents=True, exist_ok=True)
        local_pdf.write_bytes(data)
        return True
    except Exception:
        return False


def _extract(local_pdf: Path, *, base_url: str, poll_timeout_s: float, backend: str, ocr: bool):
    """Upload → poll → corpus markdown. Returns (corpus_md, doc_id)."""
    doc_id = submit_pdf(local_pdf, base_url=base_url, backend=backend, ocr=ocr)
    wait_until_done(doc_id, base_url=base_url, timeout_s=poll_timeout_s)
    return fetch_corpus_markdown(doc_id, base_url=base_url), doc_id


def _save_framework_figure(doc_id: str, local_pdf: Path, *, base_url: str) -> bool:
    """Pick the paper's framework figure (caption heuristic, fallback: first
    captioned, then first) and save it as `<id>.metrail.fig.png` + a caption
    sidecar. Best-effort: returns False when there's nothing suitable."""
    atoms = fetch_figure_atoms(doc_id, base_url=base_url)
    if not atoms:
        return False
    atom = next(
        (a for a in atoms if _FRAMEWORK_RE.search(a.get("text") or "")),
        next((a for a in atoms if (a.get("text") or "").strip()), atoms[0]),
    )
    image_url = atom.get("image_url")
    if not image_url:
        return False
    png = download_asset(image_url, base_url=base_url)
    if not png or len(png) > _MAX_FIGURE_BYTES:
        return False
    local_pdf.with_suffix(".metrail.fig.png").write_bytes(png)
    caption = (atom.get("text") or "").strip()[:300]
    local_pdf.with_suffix(".metrail.assets.json").write_text(
        json.dumps({"caption": caption}, ensure_ascii=False), encoding="utf-8"
    )
    return True


@flow(name="metrail-enrich")
def metrail_enrich(topic_id: str, limit: int = 0) -> int:
    """Extract full text for this topic's downloaded-but-unextracted papers."""
    cfg = load_topic_config_typed(default_topics_root(), topic_id)
    metrail_cfg = cfg.metrail
    with topic_run(topic_id, "metrail-enrich") as run:
        run.payload["topic_id"] = topic_id
        if metrail_cfg is not None and not metrail_cfg.enabled:
            run.payload["disabled"] = True
            return 0
        base_url = metrail_base_url(metrail_cfg.base_url if metrail_cfg else None)
        if base_url is None:
            run.payload["disabled"] = True
            return 0

        keywords = cfg.arxiv.include_keywords if cfg.arxiv else []
        poll_timeout_s = float(metrail_cfg.poll_timeout_s) if metrail_cfg else 120.0
        backend = metrail_cfg.backend if metrail_cfg else "pdfplumber"
        ocr = metrail_cfg.ocr if metrail_cfg else False

        enriched = 0
        figures = 0
        skipped: list[dict] = []
        Session = make_session_factory()
        with Session() as s:
            stmt = select(Paper).where(
                Paper.pdf_uri.is_not(None), Paper.fulltext_uri.is_(None)
            )
            kw_filter = papers_keyword_filter(keywords)
            if kw_filter is not None:
                stmt = stmt.where(kw_filter)
            if limit and limit > 0:
                stmt = stmt.limit(limit)
            for p in s.scalars(stmt).all():
                local_pdf = _local_pdf_path(p.pdf_uri or "")
                if local_pdf is None:
                    skipped.append({"arxiv_id": p.arxiv_id, "reason": "unparseable pdf_uri"})
                    continue
                if not local_pdf.is_file() and not _fetch_pdf_from_minio(p.pdf_uri, local_pdf):
                    skipped.append(
                        {"arxiv_id": p.arxiv_id, "reason": "pdf not in local mirror or minio"}
                    )
                    continue
                try:
                    corpus_md, doc_id = _extract(
                        local_pdf,
                        base_url=base_url,
                        poll_timeout_s=poll_timeout_s,
                        backend=backend,
                        ocr=ocr,
                    )
                except (MetrailError, OSError, httpx.HTTPError) as e:
                    skipped.append({"arxiv_id": p.arxiv_id, "reason": f"{type(e).__name__}: {e}"})
                    continue
                md_path = local_pdf.with_suffix(".metrail.md")
                md_path.write_text(corpus_md, encoding="utf-8")
                p.fulltext_uri = md_path.relative_to(_mirror_root()).as_posix()
                s.add(p)
                s.commit()
                enriched += 1
                # Figure is a bonus — never fails the paper (corpus is committed)
                try:
                    if _save_framework_figure(doc_id, local_pdf, base_url=base_url):
                        figures += 1
                except (MetrailError, OSError, httpx.HTTPError, ValueError):
                    pass

        run.payload["enriched"] = enriched
        run.payload["figures"] = figures
        run.payload["skipped"] = len(skipped)
        if skipped:
            run.payload["skipped_papers"] = skipped
        return enriched
