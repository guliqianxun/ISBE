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

import os
from pathlib import Path

import httpx
from prefect import flow
from sqlalchemy import select

from isbe.facts.db import make_session_factory
from isbe.observability.runs import topic_run
from isbe.topics._shared.arxiv import papers_keyword_filter
from isbe.topics._shared.metrail import (
    MetrailError,
    extract_pdf_to_markdown,
    metrail_base_url,
)
from isbe.topics.nowcasting.facts import Paper
from isbe.topics.registry import default_topics_root, load_topic_config_typed

_MIRROR_ENV = "ISBE_PAPERS_MIRROR"


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
                if local_pdf is None or not local_pdf.is_file():
                    skipped.append({"arxiv_id": p.arxiv_id, "reason": "pdf not in local mirror"})
                    continue
                try:
                    corpus_md = extract_pdf_to_markdown(
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

        run.payload["enriched"] = enriched
        run.payload["skipped"] = len(skipped)
        if skipped:
            run.payload["skipped_papers"] = skipped
        return enriched
