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
from sqlalchemy.exc import SQLAlchemyError

from isbe.facts.db import make_session_factory
from isbe.observability.runs import topic_run
from isbe.topics._shared.arxiv import papers_keyword_filter
from isbe.topics._shared.metrail import (
    MetrailError,
    download_asset,
    fetch_atoms,
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
# Core-table heuristic: main results / comparison / ablation tables
_CORE_TABLE_RE = re.compile(
    r"comparison|ablation|result|performance|state[- ]of[- ]the[- ]art|sota|"
    r"quantitative|benchmark|对比|消融|结果|性能",
    re.IGNORECASE,
)
_MAX_TABLES = 2
_MAX_TABLE_CHARS = 2500
# Low threshold: small ablation tables carry few numbers; the core-caption
# ranking (comparison/ablation first) keeps junk tables out of the top slots.
_MIN_TABLE_NUMBERS = 3
# Email-friendly cap: figures above this are skipped (data-URI inlining)
_MAX_FIGURE_BYTES = 500_000

# A paper that times out / fails this many times is blacklisted: a stuck PDF
# re-uploaded every run clogs metrail's small worker pool and starves everyone
# else. The strike counter lives in a `<id>.metrail.skip` sidecar next to the
# PDF — delete the file to retry the paper.
_MAX_STRIKES = 3


def _strikes(local_pdf: Path) -> int:
    marker = local_pdf.with_suffix(".metrail.skip")
    try:
        return int(marker.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return 0


def _add_strike(local_pdf: Path) -> None:
    marker = local_pdf.with_suffix(".metrail.skip")
    try:
        marker.write_text(str(_strikes(local_pdf) + 1), encoding="utf-8")
    except OSError:
        pass


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


def _pick_core_tables(atoms: list[dict]) -> list[dict]:
    """Rank table atoms toward the paper's evidence core: caption/text hit on
    comparison/ablation/results first, then numeric density. Tables are kept
    as the GFM markdown metrail produced — verbatim, never paraphrased."""

    def n_numbers(text: str) -> int:
        return len(re.findall(r"\d+\.?\d*", text))

    scored = []
    for a in atoms:
        if not isinstance(a, dict):
            continue
        text = (a.get("text") or "").strip()
        if not text or n_numbers(text) < _MIN_TABLE_NUMBERS:
            continue
        caption = ((a.get("metadata") or {}).get("caption") or "").strip()
        core_hit = bool(_CORE_TABLE_RE.search(caption or text[:200]))
        scored.append((not core_hit, -n_numbers(text), caption, text, a.get("page")))
    scored.sort(key=lambda s: (s[0], s[1]))
    tables = []
    for _, _, caption, text, page in scored[:_MAX_TABLES]:
        label = caption or (f"表（p{page}，自动提取）" if page is not None else "表（自动提取）")
        tables.append({"caption": label[:300], "markdown": text[:_MAX_TABLE_CHARS]})
    return tables


def _save_assets(doc_id: str, local_pdf: Path, *, base_url: str) -> tuple[bool, int]:
    """Save the framework figure PNG + core result/ablation tables as sidecars.

    Returns (figure_saved, n_tables). Best-effort throughout — assets are a
    bonus on top of the committed corpus.
    """
    meta: dict = {}
    figure_saved = False

    atoms = fetch_figure_atoms(doc_id, base_url=base_url)
    atom = next(
        (a for a in atoms if _FRAMEWORK_RE.search(a.get("text") or "")),
        next((a for a in atoms if (a.get("text") or "").strip()), atoms[0] if atoms else None),
    )
    if atom and atom.get("image_url"):
        png = download_asset(atom["image_url"], base_url=base_url)
        if png and len(png) <= _MAX_FIGURE_BYTES:
            local_pdf.with_suffix(".metrail.fig.png").write_bytes(png)
            meta["figure"] = {"caption": (atom.get("text") or "").strip()[:300]}
            figure_saved = True

    tables = _pick_core_tables(fetch_atoms(doc_id, "table", base_url=base_url))
    if tables:
        meta["tables"] = tables

    if meta:
        local_pdf.with_suffix(".metrail.assets.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
    return figure_saved, len(tables)


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
        if not keywords:
            # Without keywords the select would span EVERY topic's papers —
            # refuse rather than silently enrich the whole table.
            run.payload["disabled"] = "no arxiv include_keywords; refusing unscoped enrich"
            return 0
        poll_timeout_s = float(metrail_cfg.poll_timeout_s) if metrail_cfg else 120.0
        backend = metrail_cfg.backend if metrail_cfg else "pdfplumber"
        ocr = metrail_cfg.ocr if metrail_cfg else False

        enriched = 0
        figures = 0
        tables = 0
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
            targets = list(s.scalars(stmt).all())
            total = len(targets)
            print(f"[metrail] starting: {total} paper(s) to extract, base={base_url}", flush=True)
            for idx, p in enumerate(targets, 1):
                local_pdf = _local_pdf_path(p.pdf_uri or "")
                if local_pdf is None:
                    skipped.append({"arxiv_id": p.arxiv_id, "reason": "unparseable pdf_uri"})
                    continue
                if _strikes(local_pdf) >= _MAX_STRIKES:
                    reason = f"blacklisted after {_MAX_STRIKES} strikes"
                    skipped.append({"arxiv_id": p.arxiv_id, "reason": reason})
                    continue
                if not local_pdf.is_file() and not _fetch_pdf_from_minio(p.pdf_uri, local_pdf):
                    skipped.append(
                        {"arxiv_id": p.arxiv_id, "reason": "pdf not in local mirror or minio"}
                    )
                    continue
                print(f"[metrail] ({idx}/{total}) {p.arxiv_id} extracting...", flush=True)
                try:
                    corpus_md, doc_id = _extract(
                        local_pdf,
                        base_url=base_url,
                        poll_timeout_s=poll_timeout_s,
                        backend=backend,
                        ocr=ocr,
                    )
                    md_path = local_pdf.with_suffix(".metrail.md")
                    md_path.write_text(corpus_md, encoding="utf-8")
                    p.fulltext_uri = md_path.relative_to(_mirror_root()).as_posix()
                    s.add(p)
                    s.commit()
                except (MetrailError, ValueError, OSError, httpx.HTTPError, SQLAlchemyError) as e:
                    # per-paper skip contract: nothing here may abort the batch.
                    # ValueError: relative_to mismatch / stray parse errors;
                    # SQLAlchemyError: commit on a connection idled through
                    # n_papers × poll_timeout — roll back and move on.
                    try:
                        s.rollback()
                    except SQLAlchemyError:
                        pass
                    _add_strike(local_pdf)
                    print(
                        f"[metrail] ({idx}/{total}) SKIP {p.arxiv_id} "
                        f"(strike {_strikes(local_pdf)}/{_MAX_STRIKES}): {type(e).__name__}: {e}",
                        flush=True,
                    )
                    skipped.append({"arxiv_id": p.arxiv_id, "reason": f"{type(e).__name__}: {e}"})
                    continue
                enriched += 1
                # Assets are a bonus — never fail the paper (corpus is committed)
                try:
                    fig_saved, n_tables = _save_assets(doc_id, local_pdf, base_url=base_url)
                    if fig_saved:
                        figures += 1
                    tables += n_tables
                except (MetrailError, OSError, httpx.HTTPError, ValueError):
                    pass

        run.payload["enriched"] = enriched
        run.payload["figures"] = figures
        run.payload["tables"] = tables
        run.payload["skipped"] = len(skipped)
        if skipped:
            run.payload["skipped_papers"] = skipped
        return enriched
