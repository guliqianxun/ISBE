"""检索子系统组合：Acquire(宽召回) → Triage(语义筛) → Rank(显著性)。

这是"检索"作为可评估子系统的执行入口——digester 最终调 retrieve() 拿"已筛·已排"集合，
取代旧的"SELECT * WHERE 时间窗 + keyword ilike"。存储(写 papers 表)与此正交，不在这里。

network 仅在 acquire 内；search_fn/sleep_fn 可注入以无网测试整条组合。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from isbe.topics._shared.semantic_scholar import S2Paper, acquire
from isbe.triage.contract import RetrievalContract
from isbe.triage.models import Item, TriageResult
from isbe.triage.priority import tier_of
from isbe.triage.scorer import triage
from isbe.triage.significance import SignificanceScore, rank_significance, ranked


def s2paper_to_item(p: S2Paper) -> Item:
    pub: datetime | None = None
    if p.published_at:
        try:
            pub = datetime.fromisoformat(p.published_at)
        except ValueError:
            pub = None
    return Item(
        id=p.id, source="semantic-scholar", headline=p.title, summary=p.abstract,
        url=p.url, published_at=pub, citation_count=p.citation_count,
        fields_of_study=p.fields_of_study,
    )


@dataclass
class RetrievalResult:
    acquired: list[Item]
    per_query: dict[str, int]
    triage: TriageResult
    significance: dict[str, SignificanceScore]
    ranked_kept: list[Item]   # triage.kept 按显著性降序
    tiers: dict[str, str]     # {item_id: 'core' | 'secondary'} 显示优先级


def retrieve(
    contract: RetrievalContract,
    *,
    year_from: int,
    year_to: int,
    limit_per_query: int,
    reference_date: date,
    pub_date: str | None = None,
    search_fn=None,
    sleep_fn=None,
    log=lambda _m: None,
) -> RetrievalResult:
    extra = {} if sleep_fn is None else {"sleep_fn": sleep_fn}
    papers, per_query = acquire(
        contract.queries, year_from=year_from, year_to=year_to,
        limit_per_query=limit_per_query, pub_date=pub_date,
        search_fn=search_fn, log=log, **extra,
    )
    items = [s2paper_to_item(p) for p in papers]
    tri = triage(items, contract)
    sig = rank_significance(tri.kept, reference_date)
    tiers = tier_of(tri.kept, contract)
    return RetrievalResult(items, per_query, tri, sig, ranked(tri.kept, sig), tiers)
