"""端到端检索 demo：Acquire → Triage → Rank，打印漏斗 + 必读 + 弃因。

看"检索效果"用，不写库（存储与检索正交）。读 tests/eval/<topic>/contract.yaml。

用法：
  uv run python scripts/eval/retrieve.py nowcasting
  uv run python scripts/eval/retrieve.py nowcasting --year-from 2023 --limit 40
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
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
    ap.add_argument("--year-from", type=int, default=2024)
    ap.add_argument("--limit", type=int, default=30, help="per-query S2 cap")
    args = ap.parse_args()

    contract = _find_contract(args.topic)
    res = retrieve(
        contract, year_from=args.year_from, year_to=datetime.now(UTC).year,
        limit_per_query=args.limit, reference_date=date.today(), log=print,
    )

    n_acq, n_kept, n_drop = len(res.acquired), len(res.triage.kept), len(res.triage.dropped)
    print(f"\n=== {args.topic} 检索效果 ===")
    print(f"契约 intent: {contract.intent[:70]}")
    print(f"queries({len(contract.queries)}): {contract.queries}")
    print(f"\nACQUIRE 宽召回: {n_acq} 篇  per_query={res.per_query}")
    print(f"TRIAGE 相关性筛: 留 {n_kept} / 弃 {n_drop}")

    print("\n-- 弃（相关性筛掉，含同名异域精度泄漏）--")
    for it, reason in res.triage.dropped[:25]:
        print(f"  DROP [{reason[:30]:30}] {it.headline[:56]}")

    print("\n-- 必读（显著性置顶 top-12）--")
    for it in res.ranked_kept[:12]:
        s = res.significance[it.id]
        cc = str(s.citation_count) if s.citation_count is not None else "-"
        print(f"  {s.tier:9} cite={cc:>4} | {it.headline[:60]}")


if __name__ == "__main__":
    main()
