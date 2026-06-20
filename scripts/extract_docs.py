"""Enrichment plugin: extract full paper text + tables from local PDFs via
explore-os's docling, so report cards ground on the PAPER BODY (real
affiliations, real result-table numbers, real code URLs) — not just the abstract.

Run with explore-os's venv python (which has docling):
  E:/codes/explore-os/.venv/Scripts/python.exe scripts/extract_docs.py
Writes tmp/wf/doc_<i>.md for each kept paper.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(r"E:\codes\github\ISBE")
POOL = json.loads((REPO / "tmp" / "pool.json").read_text(encoding="utf-8"))
WF = REPO / "tmp" / "wf"


def main() -> None:
    from docling.document_converter import DocumentConverter

    conv = DocumentConverter()
    for i, r in enumerate(POOL["kept"]):
        out = WF / f"doc_{i}.md"
        pdf = r.get("pdf_path") or ""
        aid = r["arxiv_id"]
        if not pdf or not Path(pdf).is_file():
            out.write_text(f"# {aid}\n(no local PDF: {pdf})\n", encoding="utf-8")
            print(f"[{i}] {aid}: NO PDF", flush=True)
            continue
        try:
            md = conv.convert(pdf).document.export_to_markdown()
        except Exception as e:  # noqa: BLE001
            out.write_text(f"# {aid}\n(docling failed: {e})\n", encoding="utf-8")
            print(f"[{i}] {aid}: FAIL {e}", flush=True)
            continue
        # cap to keep agent context bounded: first-page region (affiliations) +
        # the rest up to ~24k chars (covers method/results/tables for most papers)
        out.write_text(md[:24000], encoding="utf-8")
        print(f"[{i}] {aid}: OK {len(md)} chars (saved {min(len(md),24000)})", flush=True)


if __name__ == "__main__":
    sys.exit(main())
