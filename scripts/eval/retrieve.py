"""端到端检索 demo：Acquire → Triage → Rank，打印漏斗 + 必读 + 弃因。

看"检索效果"用，不写库（存储与检索正交）。读 tests/eval/<topic>/contract.yaml。

用法：
  uv run python scripts/eval/retrieve.py nowcasting
  uv run python scripts/eval/retrieve.py nowcasting --year-from 2023 --limit 40
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import yaml

from isbe.triage.contract import RetrievalContract, load_contract
from isbe.triage.pipeline import retrieve

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
    ap.add_argument("--judge", action="store_true", help="跑 stage-2 LLM-judge 语义筛")
    args = ap.parse_args()

    if args.judge:
        _load_env()
    today = date.today()
    pub_date = f"{today - timedelta(days=args.since_days)}:{today}"
    contract = _find_contract(args.topic)
    res = retrieve(
        contract, year_from=today.year - 1, year_to=datetime.now(UTC).year,
        limit_per_query=args.limit, reference_date=today, pub_date=pub_date, log=print,
    )

    tri = res.triage
    n_acq = len(res.acquired)
    s1_kept = len(tri.kept)
    if args.judge:
        from isbe.triage.judge import apply_llm_judge
        print("\n[stage-2 LLM-judge 语义筛]")
        tri = apply_llm_judge(res.triage, contract, log=print)

    print(f"\n=== {args.topic} 实时检索效果（近 {args.since_days} 天）===")
    print(f"契约 intent: {contract.intent[:70]}")
    print(f"窗口 pub_date={pub_date}  queries({len(contract.queries)})")
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

    # 实时雷达：时间倒序为主（kept 已按日期降序），新颖性(cite)作辅助标注
    print("\n-- 本期新增（按时间倒序，cite 仅作辅助；新论文 cite≈0 正常）--")
    for it in tri.kept[:25]:
        s = res.significance[it.id]
        when = it.published_at.date().isoformat() if it.published_at else "??"
        cc = str(s.citation_count) if s.citation_count is not None else "-"
        tag = "★" if s.tier == "must-read" else " "
        print(f"  {tag} {when} cite={cc:>3} | {it.headline[:58]}")


if __name__ == "__main__":
    main()
