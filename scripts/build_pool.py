"""Build a REAL triaged nowcasting pool from the local arXiv DB for the
verifiability workflow. No network, no synthetic data — real abstracts.

Acquire (wide-net FTS, near window) -> triage (out_of_scope/require_any) ->
kept(core/secondary) + dropped(reason), all with abstracts + authors +
categories, dumped to tmp/pool.json for the multi-agent audit workflow.

Run: uv run python scripts/build_pool.py [since_days]
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from isbe.topics._shared.local_arxiv import DEFAULT_DB, _net_query
from isbe.topics.registry import default_topics_root, load_topic_config
from isbe.triage import Item, triage
from isbe.triage.contract import contract_from_config
from isbe.triage.priority import tier_of

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "tmp" / "pool.json"
OUT.parent.mkdir(parents=True, exist_ok=True)


def main() -> None:
    # args: [topic] [window] in any order (window = digits). Defaults: nowcasting / 14.
    topic, since_days = "nowcasting", 14
    for a in sys.argv[1:]:
        if a.isdigit():
            since_days = int(a)
        else:
            topic = a
    ref = date.today()
    cfg = load_topic_config(default_topics_root(), topic)
    contract = contract_from_config(cfg)
    assert contract is not None, f"{topic} must declare a retrieval contract"

    net = _net_query(contract)
    cutoff = (ref - timedelta(days=since_days)).isoformat()
    con = sqlite3.connect(f"file:{DEFAULT_DB}?mode=ro&immutable=1", uri=True)
    sql = (
        "select p.id, p.title, p.abstract, p.authors, p.primary_category, "
        "p.categories, p.update_date, p.hf_upvotes, p.has_pdf, p.pdf_path "
        "from papers p join papers_fts f on p.rowid = f.rowid "
        "where papers_fts match ? and p.update_date >= ? "
        "order by p.update_date desc limit 500"
    )
    rows = con.execute(sql, (net, cutoff)).fetchall()
    con.close()

    recs = {}
    for pid, title, abstract, authors, primcat, cats, upd, upv, has_pdf, pdf_path in rows:
        if pid in recs:
            continue
        recs[pid] = {
            "arxiv_id": pid,
            "title": (title or "").strip(),
            "abstract": (abstract or "").strip(),
            "authors": (authors or "").strip(),
            "primary_category": (primcat or "").strip(),
            "categories": (cats or "").strip(),
            "update_date": upd,
            "hf_upvotes": upv,
            "has_pdf": bool(has_pdf),
            "pdf_path": pdf_path or "",
            "url": f"https://arxiv.org/abs/{pid}",
        }

    items = [
        Item(id=r["arxiv_id"], source="arxiv", headline=r["title"],
             summary=r["abstract"] or None, url=r["url"], published_at=None)
        for r in recs.values()
    ]
    res = triage(items, contract)
    kept_ids = res.kept_ids
    tiers = tier_of([it for it in items if it.id in kept_ids], contract)
    drop_reason = {it.id: reason for it, reason in res.dropped}

    # Editorial curation (last-mile cases the title-only gate cannot decide):
    # apply the Coverage Auditor's semantic verdict explicitly + transparently.
    curate_path = REPO / "tmp" / f"curate_{topic}.json"
    curate = json.loads(curate_path.read_text(encoding="utf-8")) if curate_path.exists() else {}
    force_in = set(curate.get("force_include", []))
    force_out = set(curate.get("force_exclude", []))
    reasons = curate.get("reasons", {})

    kept, dropped = [], []
    for r in recs.values():
        aid = r["arxiv_id"]
        in_kept = (aid in kept_ids or aid in force_in) and aid not in force_out
        if in_kept:
            entry = {**r, "tier": tiers.get(aid, "secondary")}
            if aid in force_in:
                entry["curated"] = f"force_include: {reasons.get(aid, 'auditor: in-scope')}"
            kept.append(entry)
        else:
            reason = drop_reason.get(aid, "?")
            if aid in force_out:
                reason = f"force_exclude: {reasons.get(aid, 'auditor: out-of-scope')}"
            dropped.append({**r, "drop_reason": reason})

    # core first, then secondary; within tier, freshest first
    kept.sort(key=lambda r: (r["tier"] != "core", r["update_date"] or ""), reverse=False)
    kept.sort(key=lambda r: r["update_date"] or "", reverse=True)
    kept.sort(key=lambda r: r["tier"] != "core")

    out = {
        "topic": topic,
        "label": cfg.get("label", topic),
        "reference_date": ref.isoformat(),
        "since_days": since_days,
        "window_cutoff": cutoff,
        "net_query": net,
        "contract": {
            "intent": contract.intent,
            "in_scope": list(contract.in_scope),
            "out_of_scope": list(contract.out_of_scope),
            "require_any": list(contract.require_any),
            "secondary_terms": list(contract.secondary_terms),
            "out_of_scope_keywords": list(contract.out_of_scope_keywords),
        },
        "counts": {"pool": len(recs), "kept": len(kept), "dropped": len(dropped)},
        "kept": kept,
        "dropped": dropped,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # also emit per-agent split files for the verification workflow
    wf = REPO / "tmp" / "wf"
    wf.mkdir(parents=True, exist_ok=True)
    for old in wf.glob("kept_*.json"):
        old.unlink()
    for old in wf.glob("card_*.json"):
        old.unlink()
    for i, r in enumerate(kept):
        keep = {k: r[k] for k in ("arxiv_id", "title", "abstract", "authors",
                                  "primary_category", "update_date", "hf_upvotes",
                                  "has_pdf", "url", "tier")}
        (wf / f"kept_{i}.json").write_text(json.dumps(keep, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    _trim = lambda a, n: re.sub(r"\s+", " ", a or "").strip()[:n]
    (wf / "dropped.json").write_text(json.dumps(
        [{"arxiv_id": r["arxiv_id"], "title": r["title"],
          "primary_category": r["primary_category"], "drop_reason": r["drop_reason"],
          "abstract": _trim(r["abstract"], 320)} for r in dropped],
        ensure_ascii=False, indent=2), encoding="utf-8")
    (wf / "contract.json").write_text(json.dumps(
        {**out["contract"], "window": {"reference_date": ref.isoformat(),
         "since_days": since_days, "cutoff": cutoff}, "counts": out["counts"]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    (wf / "kept_titles.json").write_text(json.dumps(
        [{"i": i, "arxiv_id": r["arxiv_id"], "tier": r["tier"],
          "primary_category": r["primary_category"], "title": r["title"]}
         for i, r in enumerate(kept)], ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"pool={len(recs)} kept={len(kept)} dropped={len(dropped)} -> {OUT}; split -> {wf}")
    print("kept tiers:", {t: sum(1 for k in kept if k['tier'] == t) for t in ('core', 'secondary')})
    # show kept titles for a sanity glance
    for k in kept[:12]:
        ab = re.sub(r"\s+", " ", k["abstract"])[:0]  # noqa: F841
        print(f"  [{k['tier']}] {k['arxiv_id']} {k['primary_category']:14} {k['title'][:70]}")


if __name__ == "__main__":
    main()
