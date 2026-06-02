"""FT — Triage（选品与相关性）块。

设计见 docs/refactor/2026-06-03-retrieval-contract-and-eval.md。

给"相关性 / 检索质量"一个可隔离、可测试的家：位于 F2 采集与 F4 生成之间，
读一份领域**检索契约**，对采集集逐条打分 → kept/dropped + scores，
使"该搜什么 / 质量如何"从散落在 collector 配置 + LLM 兜底 + 用户脑中，
收敛为一个有输入输出、可喂 fixture、可下断言的纯函数。

边界：本块不做 IO（不碰 DB / 网络）；输入是纯 Item 数据，输出是纯数据。
打分器当前只实现级联第一阶段（规则粗筛）；第二阶段 LLM-judge 是预留接缝。
"""

from isbe.triage.contract import RetrievalContract, load_contract
from isbe.triage.eval import EvalMetrics, evaluate
from isbe.triage.models import Item, Qrel, RelevanceScore, TriageResult
from isbe.triage.scorer import triage

__all__ = [
    "EvalMetrics",
    "Item",
    "Qrel",
    "RelevanceScore",
    "RetrievalContract",
    "TriageResult",
    "evaluate",
    "load_contract",
    "triage",
]
