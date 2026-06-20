"""Enrichment: pull the FRAMEWORK figure + PERFORMANCE tables from each kept
paper's PDF via docling (explore-os venv). Verifiable artifacts straight from
the paper — the architecture diagram and the full results matrix.

Run: E:/codes/explore-os/.venv/Scripts/python.exe scripts/extract_assets.py
Writes tmp/wf/assets/fig_<i>.png + tmp/wf/assets_<i>.json per kept paper.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(r"E:\codes\github\ISBE")
POOL = json.loads((REPO / "tmp" / "pool.json").read_text(encoding="utf-8"))
WF = REPO / "tmp" / "wf"
ASSETS = WF / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)

_FRAMEWORK_RE = re.compile(
    r"architecture|framework|overview|pipeline|network|schematic|workflow|"
    r"架构|框架|结构|流程|网络|示意", re.IGNORECASE)
_RESULT_RE = re.compile(
    r"result|comparison|performance|ablation|metric|accuracy|CSI|RMSE|MAE|MSE|"
    r"score|benchmark|quantitative|对比|结果|性能|精度|消融|指标", re.IGNORECASE)
_NUM = re.compile(r"\d+\.\d+|\d+")


def _caption(item, doc) -> str:
    try:
        return (item.caption_text(doc) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def main() -> None:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.images_scale = 2.0
    opts.generate_picture_images = True
    conv = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )

    for i, r in enumerate(POOL["kept"]):
        aid = r["arxiv_id"]
        pdf = r.get("pdf_path") or ""
        man = {"arxiv_id": aid, "figure": None, "tables": []}
        if not pdf or not Path(pdf).is_file():
            (WF / f"assets_{i}.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
            print(f"[{i}] {aid}: NO PDF", flush=True)
            continue
        try:
            doc = conv.convert(pdf).document
        except Exception as e:  # noqa: BLE001
            (WF / f"assets_{i}.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
            print(f"[{i}] {aid}: convert FAIL {e}", flush=True)
            continue

        # ---- framework figure: prefer caption match, else first captioned, else first ----
        figs = []
        for p in (getattr(doc, "pictures", None) or []):
            try:
                img = p.get_image(doc)
            except Exception:  # noqa: BLE001
                img = None
            if img is None:
                continue
            figs.append((p, img, _caption(p, doc)))
        chosen = None
        for p, img, cap in figs:
            if cap and _FRAMEWORK_RE.search(cap):
                chosen = (img, cap); break
        if chosen is None:
            for p, img, cap in figs:
                if cap:
                    chosen = (img, cap); break
        if chosen is None and figs:
            chosen = (figs[0][1], figs[0][2])
        if chosen is not None:
            img, cap = chosen
            try:
                img.thumbnail((1000, 1000))
            except Exception:  # noqa: BLE001
                pass
            fp = ASSETS / f"fig_{i}.png"
            try:
                img.save(fp)
                man["figure"] = {"file": fp.name, "caption": cap[:240]}
            except Exception as e:  # noqa: BLE001
                print(f"[{i}] {aid}: fig save FAIL {e}", flush=True)

        # ---- performance tables: rank by (caption match, numeric density) ----
        scored = []
        for t in (getattr(doc, "tables", None) or []):
            try:
                md = t.export_to_markdown(doc)
            except TypeError:
                md = t.export_to_markdown()
            except Exception:  # noqa: BLE001
                md = ""
            if not md:
                continue
            cap = _caption(t, doc)
            nnum = len(_NUM.findall(md))
            cap_hit = 1 if _RESULT_RE.search(cap + " " + md[:200]) else 0
            scored.append((cap_hit, nnum, cap, md))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        for cap_hit, nnum, cap, md in scored[:2]:
            if nnum >= 4:  # a real matrix, not a 1-row layout table
                man["tables"].append({"caption": cap[:200], "markdown": md[:2500]})

        (WF / f"assets_{i}.json").write_text(json.dumps(man, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
        print(f"[{i}] {aid}: figure={'yes' if man['figure'] else 'no'} "
              f"tables={len(man['tables'])} (of {len(scored)})", flush=True)


if __name__ == "__main__":
    main()
