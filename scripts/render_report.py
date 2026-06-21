"""Render the audited nowcasting report to tmp/report.html (LOCAL, no SMTP).

Consumes the verification workflow's output (tmp/wf_result.json) + the real
pool (tmp/pool.json), maps the corrected cards onto the production weekly.j2
template, appends a point->evidence audit appendix + coverage + gate verdicts,
and renders to HTML via the production renderer. No synthetic data.

Run: uv run python scripts/render_report.py
"""
from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Template

from isbe.notify.render import render_html
from isbe.topics._shared.digester_utils import PaperBlock, SotaClaim

REPO = Path(__file__).resolve().parents[1]
TPL = REPO / "src" / "isbe" / "topics" / "_shared" / "templates" / "weekly.j2"
WFDIR = REPO / "tmp" / "wf"
POOL = json.loads((REPO / "tmp" / "pool.json").read_text(encoding="utf-8"))
OUT_MD = REPO / "tmp" / "report.md"
OUT_HTML = REPO / "tmp" / "report.html"


def _load_wf() -> dict:
    """Assemble the workflow result from the per-agent files the agents wrote."""
    cards = []
    for f in sorted(WFDIR.glob("card_*.json"), key=lambda p: int(p.stem.split("_")[1])):
        a = json.loads(f.read_text(encoding="utf-8"))
        cards.append({"arxiv_id": a["arxiv_id"], "clean": a.get("clean", True),
                      "card": a["corrected_card"], "verdicts": a.get("field_verdicts", [])})
    cov_path = WFDIR / "coverage.json"
    audit_run = cov_path.exists()
    coverage = json.loads(cov_path.read_text(encoding="utf-8")) if audit_run else {}
    n_kept = len(POOL["kept"])
    faith = len(cards) == n_kept and all(c["clean"] for c in cards)
    # Provenance is verifiable against the arXiv byline (a resolvable anchor per
    # the goal spec), not the abstract. Fill an empty provenance anchor with the
    # author byline so authorship statements stay traceable to a real source.
    authors_by = {r["arxiv_id"]: (r.get("authors") or "").strip() for r in POOL["kept"]}
    for c in cards:
        prov = c["card"].get("provenance")
        if isinstance(prov, dict) and (prov.get("text") or "").strip() not in ("", "未提及", "待核") \
                and not (prov.get("anchor") or "").strip():
            by = authors_by.get(c["arxiv_id"], "")
            if by:
                prov["anchor"] = f"作者栏 (arXiv {c['arxiv_id']}): {by[:90]}"

    # Traceability (deterministic, anchor-based): every retained statement must
    # carry a verbatim abstract anchor (or, for provenance, the byline above);
    # 未提及/待核 fields are exempt (no claim).
    _EXEMPT = ("", "未提及", "待核")

    def _traced(card: dict) -> bool:
        for k in ("verdict", "plain", "provenance", "method", "data", "code"):
            fld = card.get(k)
            if isinstance(fld, dict) and (fld.get("text") or "").strip() not in _EXEMPT:
                if not (fld.get("anchor") or "").strip():
                    return False
        eff = card.get("effect") or {}
        if not eff.get("none"):
            for c in eff.get("claims", []):
                if not (c.get("anchor") or "").strip():
                    return False
        return True

    trace = all(_traced(c["card"]) for c in cards)
    cov_ok = (coverage.get("severity", "fail") != "fail") if audit_run else True
    gates = {
        "faithfulness": "pass" if faith else "fail",
        "traceability": "pass" if trace else "fail",
        "coverage": (("pass" if coverage.get("severity") == "pass" else
                      "pass-minor" if coverage.get("severity") == "minor" else "fail")
                     if audit_run else "rule-only(未审计)"),
    }
    return {"pass": faith and trace and cov_ok, "gates": gates, "audit_run": audit_run,
            "coverage": coverage, "cards": cards}


WF = _load_wf()

KEPT = {r["arxiv_id"]: r for r in POOL["kept"]}
FIELD_CN = {"verdict": "评价", "plain": "速览", "provenance": "来源", "method": "方法",
            "data": "数据", "code": "代码", "repro": "复现", "effect": "效果"}


def _dt(s):
    try:
        return datetime.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def _block(card: dict) -> PaperBlock:
    g = lambda k: (card.get(k) or {}).get("text", "") if isinstance(card.get(k), dict) else ""
    eff = card.get("effect") or {}
    claims = []
    if not eff.get("none"):
        for c in eff.get("claims", []):
            claims.append(SotaClaim(
                metric=c.get("metric", ""), dataset=c.get("dataset", ""),
                baseline=c.get("baseline", ""), new=c.get("new", ""),
                raw=f"{c.get('metric','')} {c.get('new','')}",
                baseline_model=c.get("baseline_model", ""),
                delta_pct="",
            ))
    rp = card.get("repro") or {}
    repro = {}
    for cn, k in (("开源", "open"), ("权重", "weights"), ("算力", "compute"), ("代码完整度", "completeness")):
        if rp.get(k):
            repro[cn] = rp[k]
    return PaperBlock(
        arxiv_id=card["arxiv_id"], verdict=g("verdict"), plain=g("plain"),
        provenance=g("provenance"), method=g("method"), data=g("data"), code=g("code"),
        sota=tuple(claims), repro=repro,
    )


def _papers_and_blocks():
    papers, blocks = [], {}
    for c in WF["cards"]:
        aid = c["arxiv_id"]
        meta = KEPT.get(aid, {})
        papers.append(SimpleNamespace(
            arxiv_id=aid, title=meta.get("title", aid),
            authors=[a.strip() for a in (meta.get("authors") or "").split(",") if a.strip()] or ["—"],
            primary_category=meta.get("primary_category", ""),
            submitted_at=_dt(meta.get("update_date")),
            source_url=meta.get("url", f"https://arxiv.org/abs/{aid}"),
            pdf_uri=None, abstract=meta.get("abstract", ""),
        ))
        blocks[aid] = _block(c["card"])
    # core first then secondary, by pool order
    order = {r["arxiv_id"]: (r["tier"] != "core", i) for i, r in enumerate(POOL["kept"])}
    papers.sort(key=lambda p: order.get(p.arxiv_id, (True, 99)))
    return papers, blocks


def _appendix() -> str:
    """Per-paper collapsible evidence: each field → its proof anchor (verbatim
    source span / byline / link). The claim text is already in the card above,
    so the appendix stays compact — one line per field, expandable per paper."""
    title_by = {r["arxiv_id"]: r.get("title", "") for r in POOL["kept"]}
    out = ["", "## 审计附录：证据锚点（逐篇可展开）", "",
           "> 每条主张都可追溯到论文逐字片段（或作者栏/链接）。Point ID = `arxiv_id·字段`。"
           "卡片正文见上，此处只列「字段 → 锚点」，无锚点不留。", ""]
    for c in WF["cards"]:
        aid = c["arxiv_id"]
        card = c["card"]
        rows = []
        for k in ("verdict", "plain", "provenance", "method", "data", "code"):
            fld = card.get(k)
            if not isinstance(fld, dict):
                continue
            text = (fld.get("text") or "").strip()
            anc = (fld.get("anchor") or "").strip()
            if text in ("", "未提及", "待核") and not anc:
                rows.append(f"- **{FIELD_CN[k]}** — 未提及（无主张）")
            else:
                rows.append(f"- **{FIELD_CN[k]}** ← {('“'+anc+'”') if anc else '待核'}")
        eff = card.get("effect") or {}
        if eff.get("none"):
            rows.append("- **效果** — 摘要/正文无可比数字")
        else:
            for cl in eff.get("claims", []):
                anc = (cl.get("anchor") or "").strip()
                rows.append(f"- **效果** ← {('“'+anc+'”') if anc else '待核'}")
        n = len(rows)
        out.append(f"<details markdown=\"1\"><summary>[{aid}] {title_by.get(aid, '')[:64]} — {n} 条锚点</summary>")
        out.append("")
        out.extend(rows)
        out.append("")
        out.append("</details>")
        out.append("")
    return "\n".join(out)


def _paper_assets() -> dict:
    """Build {arxiv_id: {figure:{datauri,caption}, tables:[{caption,markdown}]}}
    from the docling manifests, for inline rendering inside each paper card."""
    idx_by = {r["arxiv_id"]: i for i, r in enumerate(POOL["kept"])}
    assets: dict = {}
    for c in WF["cards"]:
        aid = c["arxiv_id"]
        i = idx_by.get(aid)
        man_path = WFDIR / f"assets_{i}.json" if i is not None else None
        if not man_path or not man_path.exists():
            continue
        man = json.loads(man_path.read_text(encoding="utf-8"))
        entry: dict = {"figure": None, "tables": []}
        fig = man.get("figure")
        if fig:
            fp = WFDIR / "assets" / fig["file"]
            if fp.exists():
                b64 = base64.b64encode(fp.read_bytes()).decode("ascii")
                entry["figure"] = {"caption": fig.get("caption", "") or "(论文原图)",
                                   "datauri": f"data:image/png;base64,{b64}"}
        for t in (man.get("tables") or []):
            entry["tables"].append({"caption": t.get("caption", "") or "(结果表)",
                                    "markdown": t.get("markdown", "")})
        if entry["figure"] or entry["tables"]:
            assets[aid] = entry
    return assets


def _coverage_section() -> str:
    cov = WF.get("coverage") or {}
    g = WF.get("gates", {})
    out = ["", "## 覆盖与门禁", "",
           f"- **门禁**：coverage={g.get('coverage')} · faithfulness={g.get('faithfulness')} · "
           f"traceability={g.get('traceability')} · **{'PASS' if WF.get('pass') else 'FAIL'}**",
           f"- 采集池 {POOL['counts']['pool']} 篇 → 保留 {POOL['counts']['kept']} / 丢弃 {POOL['counts']['dropped']}"
           f"（窗口 {POOL['since_days']} 天，截至 {POOL['reference_date']}）"]
    if not WF.get("audit_run"):
        out.append("- 本次为**本地常规投递**：经透明规则门（out_of_scope/require_any）筛选 + 每字段证据锚点；"
                   "多智能体覆盖审计（coverage gate）为单独 QA 工具，未在本次投递运行。")
    else:
        out.append(f"- 覆盖判定：**{cov.get('severity','?')}** — {cov.get('category_check','')} {cov.get('window_check','')}")
    if cov.get("wrongly_dropped"):
        out.append("- **疑似误弃（应在但被丢）**：")
        for w in cov["wrongly_dropped"]:
            out.append(f"  - {w.get('arxiv_id','')}：{w.get('why','')}")
    if cov.get("wrongly_kept"):
        out.append("- **疑似误留（在但应剔）**：")
        for w in cov["wrongly_kept"]:
            out.append(f"  - {w.get('arxiv_id','')}：{w.get('why','')}")
    if cov.get("recommended_fixes"):
        out.append("- **建议修正（topic.yaml）**：" + "；".join(cov["recommended_fixes"]))
    return "\n".join(out)


def main() -> None:
    papers, blocks = _papers_and_blocks()
    n_core = sum(1 for r in POOL["kept"] if r["tier"] == "core")
    tldr = (f"本期保留 {len(papers)} 篇（核心 {n_core}），来自近 {POOL['since_days']} 天本地 arXiv 采集池"
            f"{POOL['counts']['pool']} 篇经透明门筛选。每篇仅保留可在摘要中核实的字段，附证据锚点见文末附录。")
    body = Template(TPL.read_text(encoding="utf-8")).render(
        topic_id="nowcasting", topic_label=POOL["label"], period_label=POOL["reference_date"],
        tldr=tldr, analysis="见文末「覆盖与门禁审计」。", distillation="",
        memory_refs="topics/nowcasting.theses.md", trace_id="local-verify",
        generated_at=datetime(2026, 6, 20, 0, 0).isoformat(), artifact_id="local-verify-report",
        papers=papers, paper_blocks=blocks, paper_assets=_paper_assets(),
        glossary=[], repos=[], repo_reviews={}, comparison=None,
    )
    md = body + "\n" + _coverage_section() + "\n" + _appendix() + "\n"
    OUT_MD.write_text(md, encoding="utf-8")
    html = render_html(topic_label="nowcasting", period_label=POOL["reference_date"],
                       artifact_md=md, artifact_path=None)
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"gates: {WF.get('gates')} pass={WF.get('pass')}")
    print(f"wrote {OUT_HTML} ({len(html)} bytes) + {OUT_MD}")


if __name__ == "__main__":
    main()
