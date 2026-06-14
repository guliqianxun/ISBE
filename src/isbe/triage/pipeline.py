"""检索子系统组合：Acquire(宽召回) → Triage(语义筛) → Rank(显著性)。

这是"检索"作为可评估子系统的执行入口——digester 最终调 retrieve() 拿"已筛·已排"集合，
取代旧的"SELECT * WHERE 时间窗 + keyword ilike"。存储(写 papers 表)与此正交，不在这里。

network 仅在 acquire 内；search_fn/sleep_fn 可注入以无网测试整条组合。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

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


def acquire_by_source(
    contract: RetrievalContract,
    *,
    reference_date: date,
    since_days: int,
    limit: int,
    log=lambda _m: None,
) -> tuple[list[S2Paper], dict[str, int]]:
    """按 contract.source 选采集源（生产 A 用）。local=本地每日 papers.db；s2=Semantic Scholar。"""
    if contract.source == "local":
        from isbe.topics._shared.local_arxiv import acquire_local
        return acquire_local(
            contract, reference_date=reference_date, since_days=since_days,
            limit=max(limit, 500), log=log,
        )
    cutoff = reference_date - timedelta(days=since_days)
    return acquire(
        contract.queries, year_from=reference_date.year - 1, year_to=reference_date.year,
        limit_per_query=limit, pub_date=f"{cutoff}:{reference_date}", log=log,
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
    reference_date: date,
    year_from: int = 0,
    year_to: int = 0,
    limit_per_query: int = 0,
    pub_date: str | None = None,
    search_fn=None,
    sleep_fn=None,
    log=lambda _m: None,
    papers: list[S2Paper] | None = None,
    per_query: dict[str, int] | None = None,
) -> RetrievalResult:
    """papers 已给则直接用（本地源等）；否则走 S2 acquire。"""
    if papers is None:
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
    return RetrievalResult(items, per_query or {}, tri, sig, ranked(tri.kept, sig), tiers)
