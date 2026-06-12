"""Triage 块的纯数据类型（无 IO、无 ORM）。

Item 故意不复用 facts.Article（ORM），以保持本块与存储层解耦：
triage 对"采集集快照"打分，快照可以来自 DB、来自冻结的 fixture jsonl、
或来自实时抓取——triage 不关心来源。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Item:
    """采集集中的一条原子项（过滤前的原始条目）。"""

    id: str
    source: str
    headline: str
    summary: str | None = None
    url: str = ""
    published_at: datetime | None = None
    lang: str = "en"
    # 可选元数据（论文源给，RSS 源为空）。RC5 显著性 / RC6 谱系消费。
    citation_count: int | None = None
    fields_of_study: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        """打分用的可检索文本 = 标题 + 摘要。"""
        return f"{self.headline}\n{self.summary or ''}"


@dataclass(frozen=True)
class RelevanceScore:
    """对单条 Item 的相关性判定（可审计：带理由 + 命中标记）。"""

    item_id: str
    relevant: bool
    score: float  # 0..1
    reason: str
    matched: tuple[str, ...] = ()
    stage: str = "rule"  # rule | llm — 哪一级做出的判定


@dataclass
class TriageResult:
    """triage() 的输出：留/弃 + 每条分数。全是数据，可断言。"""

    kept: list[Item] = field(default_factory=list)
    dropped: list[tuple[Item, str]] = field(default_factory=list)  # (item, 弃因)
    scores: dict[str, RelevanceScore] = field(default_factory=dict)

    @property
    def kept_ids(self) -> set[str]:
        return {it.id for it in self.kept}


@dataclass(frozen=True)
class Qrel:
    """一条相关性判定（Cranfield/TREC qrels）。由人工标注，是 ground truth。

    rel: 0=不相关 1=相关 2=高价值。二值化规则 rel>=1 即相关（钉死，防 borderline 漂移）。
    must_hit: 锚点——本期若出现则 triage 必须留下（硬约束）。
    """

    item_id: str
    rel: int
    must_hit: bool = False
    note: str = ""

    @property
    def is_relevant(self) -> bool:
        return self.rel >= 1


@dataclass(frozen=True)
class EvalMetrics:
    """triage 侧检索指标（set-based）。

    注：生产应委托 pytrec_eval 计算（见设计 §8 adopt-vs-build）；此处手算，
    用于最小检验闭环，避免提前引入依赖。precision/recall 相对**已判定的采集集**，
    故 recall 是真召回（采集集穷尽可标），不是池化近似。
    """

    precision: float
    recall: float
    f1: float
    anchor_recall: float
    n_kept: int
    n_relevant: int
    n_judged: int
