"""检索组合管道测试（注入 search_fn，无网络）。

验 Acquire→Triage→Rank 串起来：同名异域(经济 nowcasting)被筛掉，真域里程碑置顶。
"""

from __future__ import annotations

from datetime import date

from isbe.triage.contract import RetrievalContract
from isbe.triage.pipeline import retrieve

_NOSLEEP = lambda _s: None  # noqa: E731


def _raw(pid, arxiv, title, cite, when):
    return {
        "paperId": pid, "title": title, "abstract": title, "year": int(when[:4]),
        "publicationDate": when, "citationCount": cite,
        "externalIds": {"ArXiv": arxiv}, "fieldsOfStudy": ["Computer Science"],
    }


def test_pipeline_filters_offdomain_and_ranks_milestones():
    contract = RetrievalContract(
        intent="precipitation nowcasting",
        queries=["precipitation nowcasting"],
        out_of_scope_keywords=["GDP", "economic", "influenza"],
    )
    raws = [
        _raw("p1", "2401.001", "DGMR: skillful radar precipitation nowcasting", 300, "2024-01-01"),
        _raw("p2", "2402.002", "GDP nowcasting with machine learning", 80, "2024-02-01"),
        _raw("p3", "2403.003", "Deep learning precipitation nowcasting", 10, "2024-03-01"),
        _raw("p4", "2404.004", "Influenza nowcasting from search trends", 40, "2024-04-01"),
    ]
    res = retrieve(
        contract, year_from=2024, year_to=2026, limit_per_query=10,
        reference_date=date(2026, 6, 8), search_fn=lambda _q: raws, sleep_fn=_NOSLEEP,
    )

    assert len(res.acquired) == 4
    dropped_titles = [it.headline for it, _ in res.triage.dropped]
    assert any("GDP" in t for t in dropped_titles)        # 经济 nowcasting 筛掉
    assert any("Influenza" in t for t in dropped_titles)  # 疾病 nowcasting 筛掉
    kept_ids = res.triage.kept_ids
    assert "2401.001" in kept_ids and "2403.003" in kept_ids  # 真降水域留下
    assert res.ranked_kept[0].id == "2401.001"            # 高引里程碑 DGMR 置顶
