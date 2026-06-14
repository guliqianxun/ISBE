"""端到端检索 demo：Acquire → Triage → Rank，打印漏斗 + 必读 + 弃因。

看"检索效果"用，不写库（存储与检索正交）。读 tests/eval/<topic>/contract.yaml。

用法：
  uv run python scripts/eval/retrieve.py nowcasting
  uv run python scripts/eval/retrieve.py nowcasting --year-from 2023 --limit 40
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import yaml

from isbe.triage.contract import RetrievalContract, load_contract
from isbe.triage.pipeline import acquire_by_source, retrieve

REPO = Path(__file__).resolve().parents[2]


def _load_env() -> None:
    """把 .env 的 KEY=VALUE 注入 os.environ（仅当未设），供 LLM-judge 取 key。"""
    import os
    env = REPO / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


def _find_contract(topic: str) -> RetrievalContract:
    flat = REPO / "tests" / "eval" / topic / "contract.yaml"
    if flat.exists():
        return load_contract(flat)
    snaps = sorted((REPO / "tests" / "eval" / topic).glob("*/contract.yaml"))
    if snaps:
        return load_contract(snaps[-1])
    ty = REPO / "src" / "isbe" / "topics" / topic.replace("-", "_") / "topic.yaml"
    if ty.exists():
        cfg = yaml.safe_load(ty.read_text(encoding="utf-8")) or {}
        if cfg.get("retrieval"):
            return RetrievalContract.model_validate(cfg["retrieval"])
    raise SystemExit(f"no retrieval contract for topic {topic!r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("topic")
    ap.add_argument("--since-days", type=int, default=30,
                    help="实时雷达近窗：只取近 N 天发表（默认 30）")
    ap.add_argument("--limit", type=int, default=40, help="per-query S2 cap")
    ap.add_argument("--source", choices=["s2", "local"], default=None,
                    help="覆盖契约 source; 缺省用 contract.source。local=本地每日 papers.db; s2=S2")
    ap.add_argument("--judge", action="store_true", help="跑 stage-2 LLM-judge 语义筛")
    ap.add_argument("--dump", type=str, default=None,
                    help="把全部判定(IN/OUT+理由)落成 jsonl，当范围参考用")
    args = ap.parse_args()

    if args.judge:
        _load_env()
    today = date.today()
    contract = _find_contract(args.topic)
    if args.source:                       # --source 覆盖契约声明
        contract = contract.model_copy(update={"source": args.source})
    papers, per_query = acquire_by_source(
        contract, reference_date=today, since_days=args.since_days, limit=args.limit, log=print,
    )
    res = retrieve(contract, reference_date=today, papers=papers, per_query=per_query)

    tri = res.triage
    n_acq = len(res.acquired)
    s1_kept = len(tri.kept)
    if args.judge:
        from isbe.triage.judge import apply_llm_judge
        print("\n[stage-2 LLM-judge 语义筛]")
        tri = apply_llm_judge(res.triage, contract, log=print)

    print(f"\n=== {args.topic} 实时检索效果（近 {args.since_days} 天）===")
    print(f"契约 intent: {contract.intent[:70]}")
    print(f"source={contract.source}  窗口=近{args.since_days}天  queries({len(contract.queries)})")
    print(f"\nACQUIRE 近窗召回: {n_acq} 篇  per_query={res.per_query}")
    nk, nd = len(tri.kept), len(tri.dropped)
    if args.judge:
        print(f"TRIAGE: stage-1 留 {s1_kept} -> stage-2 judge 留 {nk} / 弃 {nd}")
    else:
        print(f"TRIAGE 范围筛(仅 stage-1): 留 {nk} / 弃 {nd}")

    if tri.dropped:
        print("\n-- 弃（范围外）--")
        for it, reason in tri.dropped[:25]:
            print(f"  DROP [{reason[:34]:34}] {it.headline[:48]}")

    # 显示优先级分层：核心(0-6h 雷达短临)置顶，相关次级靠后；层内时间倒序
    def _show(it):
        when = it.published_at.date().isoformat() if it.published_at else "??"
        print(f"    {when} | {it.headline[:62]}")

    core = [it for it in tri.kept if res.tiers.get(it.id) == "core"]
    secondary = [it for it in tri.kept if res.tiers.get(it.id) == "secondary"]
    print(f"\n== 核心层（{len(core)}）按时间倒序 ==")
    for it in core[:25]:
        _show(it)
    if secondary:
        print(f"\n== 相关次级层（{len(secondary)}，命中 secondary_terms）==")
        for it in secondary[:20]:
            _show(it)

    if args.dump:
        import json
        rows = []
        for it in tri.kept:
            sc = tri.scores.get(it.id)
            rows.append({
                "item_id": it.id, "verdict": "IN",
                "stage": sc.stage if sc else "", "reason": sc.reason if sc else "",
                "published_at": it.published_at.date().isoformat() if it.published_at else None,
                "headline": it.headline, "url": it.url,
            })
        for it, reason in tri.dropped:
            rows.append({
                "item_id": it.id, "verdict": "OUT",
                "stage": "rule" if reason.startswith("out_of_scope") else "llm",
                "reason": reason,
                "published_at": it.published_at.date().isoformat() if it.published_at else None,
                "headline": it.headline, "url": it.url,
            })
        out = Path(args.dump)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\n判定清单已落: {args.dump}  ({len(rows)} 行: {nk} IN / {nd} OUT)")


if __name__ == "__main__":
    main()
