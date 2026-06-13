"""Acquire 宽召回引擎离线测试（注入 search_fn，无网络）。

验并集/去重/query_hit/失败标记/arxiv-id 映射——freezer 与未来生产 collector 共用这套。
"""

from __future__ import annotations

from isbe.topics._shared.semantic_scholar import S2Paper, acquire

_NOSLEEP = lambda _s: None  # noqa: E731


def _raw(pid, arxiv=None, title="t", year=2025, cite=10, date=None):
    return {
        "paperId": pid,
        "title": title,
        "abstract": "abstract",
        "year": year,
        "publicationDate": date,
        "citationCount": cite,
        "externalIds": {"ArXiv": arxiv} if arxiv else {},
        "fieldsOfStudy": ["Computer Science"],
    }


def _acq(queries, search_fn):
    return acquire(
        queries, year_from=2025, year_to=2026, limit_per_query=10,
        search_fn=search_fn, sleep_fn=_NOSLEEP,
    )


def test_acquire_unions_dedups_and_sorts():
    responses = {
        "q1": [_raw("p1", arxiv="2501.001", date="2025-01-05"),
               _raw("p2", arxiv="2502.002", date="2025-02-05")],
        "q2": [_raw("p2", arxiv="2502.002", date="2025-02-05"),   # dup of q1
               _raw("p3", arxiv="2503.003", date="2025-03-05")],
    }
    papers, per = _acq(["q1", "q2"], lambda q: responses.get(q))

    assert [p.id for p in papers] == ["2503.003", "2502.002", "2501.001"]  # 日期降序
    assert len(papers) == 3                       # p2 去重
    assert per == {"q1": 2, "q2": 1}              # q2 只新增 p3
    byid = {p.id: p for p in papers}
    assert byid["2502.002"].query_hit == "q1"     # 首个命中的查询


def test_acquire_marks_failed_query():
    def sfn(q):
        return None if q == "bad" else [_raw("p1", arxiv="2501.001")]
    papers, per = _acq(["good", "bad"], sfn)
    assert per["good"] == 1 and per["bad"] == -1
    assert len(papers) == 1                        # 失败查询不阻断其余


def test_acquire_falls_back_to_s2_id_when_no_arxiv():
    papers, _ = _acq(["q"], lambda q: [_raw("ponly")])  # 无 externalIds.ArXiv
    assert isinstance(papers[0], S2Paper)
    assert papers[0].id == "s2:ponly" and papers[0].arxiv_id is None


def test_acquire_carries_metadata_for_rc5():
    papers, _ = _acq(["q"], lambda q: [_raw("p", arxiv="2501.001", cite=349)])
    assert papers[0].citation_count == 349
    assert papers[0].fields_of_study == ("Computer Science",)
