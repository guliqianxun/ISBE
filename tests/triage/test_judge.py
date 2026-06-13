"""stage-2 LLM-judge 测试（注入 complete_fn，无 LLM/网络）。

验语义筛 IN/OUT、级联移弃、整批失败/漏判 recall-safe 默认 IN。
"""

from __future__ import annotations

from isbe.triage.contract import RetrievalContract
from isbe.triage.judge import apply_llm_judge, judge_items
from isbe.triage.models import Item, TriageResult

CONTRACT = RetrievalContract(
    intent="降水临近预报",
    in_scope=["降水 0-6h 短临"],
    out_of_scope=["军用/生物医学雷达", "疾病 nowcasting"],
)
ITEMS = [
    Item(id="a", source="s", headline="DGMR skillful precipitation nowcasting"),
    Item(id="b", source="s", headline="Cognitive radar for missile tracking"),
    Item(id="c", source="s", headline="mmWave radar heart rate detection"),
]

_VERDICT = "1|IN|radar-extrapolation|降水短临\n2|OUT||军用雷达\n3|OUT||生物医学雷达"


def test_judge_classifies_in_and_out():
    scores = judge_items(ITEMS, CONTRACT, complete_fn=lambda _s, _u: _VERDICT)
    assert scores["a"].relevant is True
    assert scores["b"].relevant is False and scores["c"].relevant is False
    assert scores["a"].matched == ("radar-extrapolation",)
    assert all(s.stage == "llm" for s in scores.values())


def test_apply_llm_judge_moves_out_to_dropped():
    stage1 = TriageResult(kept=list(ITEMS))   # 假设 stage-1 全留
    refined = apply_llm_judge(stage1, CONTRACT, complete_fn=lambda _s, _u: _VERDICT)
    assert refined.kept_ids == {"a"}
    assert {it.id for it, _ in refined.dropped} == {"b", "c"}


def test_unparsed_output_keeps_recall_safe():
    scores = judge_items(ITEMS, CONTRACT, complete_fn=lambda _s, _u: "garbage, no verdicts")
    assert all(s.relevant for s in scores.values())   # 漏判默认 IN


def test_batch_exception_keeps_recall_safe():
    def boom(_s, _u):
        raise RuntimeError("api down")
    scores = judge_items(ITEMS, CONTRACT, complete_fn=boom)
    assert all(s.relevant for s in scores.values())
