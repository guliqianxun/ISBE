"""真实采集集上的 triage 回归（TDD 靶子）。

设计见 docs/refactor/2026-06-03-retrieval-contract-and-eval.md §5。

冻结的采集集（freeze_collection.py 产出）+ 人工 qrels → 断言 triage 达标。
**qrels 必须由人工标注**（相关性的 ground truth 在用户脑中，不可由程序捏造）。
未标注前：本测试 skip 并提示如何标。一旦某快照存在 qrels.jsonl，自动转为活跃回归。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from isbe.triage import evaluate, load_contract, triage
from isbe.triage.models import Item, Qrel

EVAL_ROOT = Path(__file__).parent / "motorcycle"

# 门槛（设计 §4.2）
PRECISION_BAR = 0.8
RECALL_BAR = 0.9
ANCHOR_BAR = 1.0


def _load_collection(path: Path) -> list[Item]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        pub = r.get("published_at")
        items.append(Item(
            id=r["id"], source=r["source"], headline=r["headline"],
            summary=r.get("summary"), url=r.get("url", ""),
            published_at=datetime.fromisoformat(pub) if pub else None,
            lang=r.get("lang", "en"),
        ))
    return items


def _load_qrels(path: Path) -> list[Qrel]:
    qrels = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("rel") is None:  # 未标的行（模板残留）跳过
            continue
        qrels.append(Qrel(
            item_id=r["item_id"], rel=int(r["rel"]),
            must_hit=bool(r.get("must_hit", False)), note=r.get("note", ""),
        ))
    return qrels


def _labeled_snapshots() -> list[Path]:
    return sorted(EVAL_ROOT.glob("*/qrels.jsonl"))


@pytest.mark.skipif(
    not _labeled_snapshots(),
    reason=(
        "无已标注 qrels。先标注：编辑 tests/eval/motorcycle/<snapshot>/qrels.template.jsonl "
        "（每行填 rel: 0|1|2，锚点设 must_hit: true），另存为 qrels.jsonl。"
    ),
)
@pytest.mark.parametrize("qrels_path", _labeled_snapshots(), ids=lambda p: p.parent.name)
def test_motorcycle_triage_meets_bar(qrels_path: Path):
    snap = qrels_path.parent
    items = _load_collection(snap / "collection.jsonl")
    contract = load_contract(snap / "contract.yaml")
    qrels = _load_qrels(qrels_path)

    result = triage(items, contract)
    m = evaluate(result, qrels)

    assert m.anchor_recall >= ANCHOR_BAR, f"必命中漏了: anchor_recall={m.anchor_recall}"
    assert m.precision >= PRECISION_BAR, f"精度不足: precision={m.precision:.3f} (kept {m.n_kept})"
    assert m.recall >= RECALL_BAR, f"召回不足: recall={m.recall:.3f}"
