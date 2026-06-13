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
    args = ap.parse_args()

    today = date.today()
    pub_date = f"{today - timedelta(days=args.since_days)}:{today}"
    contract = _find_contract(args.topic)
    res = retrieve(
        contract, year_from=today.year - 1, year_to=datetime.now(UTC).year,
        limit_per_query=args.limit, reference_date=today, pub_date=pub_date, log=print,
    )

    n_acq, n_kept, n_drop = len(res.acquired), len(res.triage.kept), len(res.triage.dropped)
    print(f"\n=== {args.topic} 实时检索效果（近 {args.since_days} 天）===")
    print(f"契约 intent: {contract.intent[:70]}")
    print(f"窗口 pub_date={pub_date}  queries({len(contract.queries)})")
    print(f"\nACQUIRE 近窗召回: {n_acq} 篇  per_query={res.per_query}")
    print(f"TRIAGE 范围筛: 留 {n_kept} / 弃 {n_drop}")

    if res.triage.dropped:
        print("\n-- 弃（范围外）--")
        for it, reason in res.triage.dropped[:20]:
            print(f"  DROP [{reason[:28]:28}] {it.headline[:54]}")

    # 实时雷达：时间倒序为主（kept 已按日期降序），新颖性(cite)作辅助标注
    print("\n-- 本期新增（按时间倒序，cite 仅作辅助；新论文 cite≈0 正常）--")
    for it in res.triage.kept[:20]:
        s = res.significance[it.id]
        when = it.published_at.date().isoformat() if it.published_at else "??"
        cc = str(s.citation_count) if s.citation_count is not None else "-"
        tag = "★" if s.tier == "must-read" else " "
        print(f"  {tag} {when} cite={cc:>3} | {it.headline[:58]}")


if __name__ == "__main__":
    main()
