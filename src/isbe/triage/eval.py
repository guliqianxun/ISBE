"""evaluate() — 用 qrels 给 TriageResult 打分（Cranfield 范式）。

set-based precision / recall / F1 + anchor_recall。指标相对**已判定集**，
故 recall 是真召回（采集集穷尽可标，免池化）。

生产应委托 pytrec_eval（见设计 §8）；此处手算以闭合最小检验，不提前引入依赖。
"""

from __future__ import annotations

from collections.abc import Iterable

from isbe.triage.models import EvalMetrics, Qrel, TriageResult


def evaluate(result: TriageResult, qrels: Iterable[Qrel]) -> EvalMetrics:
    qmap = {q.item_id: q for q in qrels}
    relevant_ids = {q.item_id for q in qmap.values() if q.is_relevant}
    musthit_ids = {q.item_id for q in qmap.values() if q.must_hit}

    kept_ids = result.kept_ids
    # 只在有判定的项上算 precision（穷尽标注下 kept ⊆ judged）
    judged_kept = {i for i in kept_ids if i in qmap}
    tp = len(judged_kept & relevant_ids)

    precision = tp / len(judged_kept) if judged_kept else 0.0
    recall = tp / len(relevant_ids) if relevant_ids else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    anchor_recall = (
        len(kept_ids & musthit_ids) / len(musthit_ids) if musthit_ids else 1.0
    )

    return EvalMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        anchor_recall=anchor_recall,
        n_kept=len(kept_ids),
        n_relevant=len(relevant_ids),
        n_judged=len(qmap),
    )
