"""验证 triage+eval 的**机器本身正确**（合成数据，无真实相关性主张）。

这一层证明：指标计算对、且能区分好坏 triage —— keep-all 基线达不到精度门槛，
规则 triage 能达到。它不依赖任何人工标注，故现在即可绿。
真实摩托车相关性的检验在 test_motorcycle_triage.py（等人工 qrels）。
"""

from __future__ import annotations

from isbe.triage import RetrievalContract, evaluate, triage
from isbe.triage.models import Item, Qrel, TriageResult

CONTRACT = RetrievalContract(
    intent="200cc+ 摩托车市场调研",
    out_of_scope_keywords=["scooter", "moped", "50cc"],
    entity_terms=["Kawasaki", "Yamaha"],
)

ITEMS = [
    Item(id="s1", source="x", headline="Kawasaki Ninja 500 launch", summary="new 451cc sport bike"),
    Item(id="s2", source="x", headline="Best 50cc scooter for the city", summary="cheap commuter"),
    Item(id="s3", source="x", headline="Yamaha MT-07 long term review", summary="689cc naked"),
    Item(id="s4", source="x", headline="New moped commuter unveiled", summary="urban moped"),
]

QRELS = [
    Qrel("s1", rel=2, must_hit=True),   # 新车，必命中
    Qrel("s2", rel=0),                  # scooter / 50cc，不相关
    Qrel("s3", rel=1),                  # 评测，相关
    Qrel("s4", rel=0),                  # moped，不相关
]


def test_rule_triage_drops_out_of_scope_and_keeps_relevant():
    result = triage(ITEMS, CONTRACT)
    assert result.kept_ids == {"s1", "s3"}
    assert {it.id for it, _ in result.dropped} == {"s2", "s4"}
    # 弃因可审计
    assert "50cc" in result.scores["s2"].reason or "scooter" in result.scores["s2"].reason


def test_metrics_are_correct_for_rule_triage():
    m = evaluate(triage(ITEMS, CONTRACT), QRELS)
    assert m.precision == 1.0       # 留下的 s1,s3 都相关
    assert m.recall == 1.0          # 所有相关项都留了
    assert m.anchor_recall == 1.0   # 必命中 s1 留了
    assert m.n_judged == 4


def test_out_of_scope_matches_title_only_not_abstract():
    """recall-safe：out_of_scope 关键词只在摘要(动机句)出现，不应被 stage-1 误杀。

    nowcasting live 实测：摘要里 'reduce economic losses' 让规则全文匹配误杀 7/7 真域论文。
    规则阶段只匹配标题；语义级 out-of-scope 交 stage-2 LLM-judge。
    """
    it = Item(
        id="x", source="s",
        headline="Radar precipitation nowcasting with diffusion",  # 标题无 out_of_scope 词
        summary="Accurate nowcasting reduces economic losses from floods.",  # 摘要含 'economic'
    )
    c = RetrievalContract(intent="precip nowcasting", out_of_scope_keywords=["economic"])
    res = triage([it], c)
    assert it in res.kept


def test_keep_all_baseline_fails_precision_bar():
    """证明指标能区分：keep-all 把噪音全留 → precision 0.5 < 0.8 门槛。"""
    keep_all = TriageResult(kept=list(ITEMS))
    m = evaluate(keep_all, QRELS)
    assert m.precision == 0.5       # 4 留 2 相关
    assert m.recall == 1.0          # 召回满，但精度差
    assert m.precision < 0.8        # 达不到门槛 —— 这正是"全留"不可接受的量化证据
