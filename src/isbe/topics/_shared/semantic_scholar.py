"""Semantic Scholar 论文获取 —— Acquire 宽召回引擎（RC2/RC3）。

为什么是 S2 而非 arxiv API：本机（CN + 共享代理出口）直连 arxiv 被 WAF 硬封
（429 / 断连，实测），arxiv API 仅服务器侧可达；S2 退避后可达，且额外给
citationCount / fieldsOfStudy / externalIds.ArXiv —— 正好喂 RC5 显著性 / RC7 归类 /
RC6 谱系，并用 arxiv_id 桥回现有 papers 管线。

策略：契约的 queries（每面一条/多条）→ 各自查 S2 → 并集去重 → 比单一窄查询更宽的池。
**只做去重，不做相关性过滤**——相关性留给下游 triage（撒大网，召回导向）。

可测性：网络隔离在 default_get；acquire 接受可注入 search_fn，并集/去重逻辑无网可测。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "title,abstract,year,publicationDate,citationCount,externalIds,fieldsOfStudy,authors"
_UA = {"User-Agent": "ISBE-acquire/0.1 (mailto:visitorindark@gmail.com)"}

# search_fn(query) -> list[dict] | None（None = 该查询退避耗尽失败）
SearchFn = Callable[[str], "list[dict] | None"]


@dataclass(frozen=True)
class S2Paper:
    id: str                       # arxiv_id 或 "s2:<paperId>"
    arxiv_id: str | None
    title: str
    abstract: str | None
    published_at: str | None      # ISO date 或 None
    url: str
    citation_count: int | None
    fields_of_study: tuple[str, ...]
    query_hit: str                # 首个命中它的查询


def default_get(url: str, *, tries: int = 7, sleep_fn=time.sleep, log=print) -> dict | None:
    """S2 未授权限速重，退避重试清 429。失败返回 None。"""
    for i in range(tries):
        try:
            r = httpx.get(url, headers=_UA, timeout=45.0)
            if r.status_code == 200:
                return r.json()
            log(f"    (try {i + 1}: HTTP {r.status_code}, backoff)")
        except Exception as ex:  # noqa: BLE001
            log(f"    (try {i + 1}: {type(ex).__name__}, backoff)")
        sleep_fn(4 * (i + 1))
    return None


def _to_paper(p: dict, query_hit: str) -> S2Paper | None:
    ext = p.get("externalIds") or {}
    arxiv_id = ext.get("ArXiv")
    pid = p.get("paperId")
    if not (arxiv_id or pid):
        return None
    year = p.get("year")
    return S2Paper(
        id=arxiv_id or f"s2:{pid}",
        arxiv_id=arxiv_id,
        title=(p.get("title") or "").strip(),
        abstract=(p.get("abstract") or "").strip() or None,
        published_at=p.get("publicationDate") or (f"{year}-01-01" if year else None),
        url=(
            f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id
            else f"https://www.semanticscholar.org/paper/{pid}"
        ),
        citation_count=p.get("citationCount"),
        fields_of_study=tuple(p.get("fieldsOfStudy") or ()),
        query_hit=query_hit,
    )


def s2_search(
    query: str, *, year_from: int, year_to: int, limit: int, get_fn=default_get
) -> list[dict] | None:
    """单条 S2 查询，返回 raw paper dict 列表；失败（退避耗尽）返回 None。"""
    qp = httpx.QueryParams({"q": query})["q"]
    url = f"{S2_SEARCH}?query={qp}&year={year_from}-{year_to}&limit={limit}&fields={FIELDS}"
    d = get_fn(url)
    if d is None:
        return None
    return d.get("data") or []


def acquire(
    queries: list[str],
    *,
    year_from: int,
    year_to: int,
    limit_per_query: int,
    search_fn: SearchFn | None = None,
    sleep_fn=time.sleep,
    polite_sleep: float = 4.0,
    log: Callable[[str], None] = lambda _m: None,
) -> tuple[list[S2Paper], dict[str, int]]:
    """多查询并集去重，返回 (papers 按日期降序, per_query 计数)。

    per_query[q] = -1 表示该查询失败；否则为该查询新增（去重后）条数。
    相关性不在这里判——只构池。search_fn 可注入以无网测试。
    """
    if search_fn is None:
        def search_fn(q: str) -> list[dict] | None:
            return s2_search(q, year_from=year_from, year_to=year_to, limit=limit_per_query)

    by_id: dict[str, S2Paper] = {}
    per_query: dict[str, int] = {}
    for q in queries:
        data = search_fn(q)
        if data is None:
            per_query[q] = -1
            log(f"  query {q!r}: FAILED after backoff")
            continue
        added = 0
        for raw in data:
            paper = _to_paper(raw, q)
            if paper is None or paper.id in by_id:
                continue
            by_id[paper.id] = paper
            added += 1
        per_query[q] = added
        log(f"  query {q!r}: +{added} new (fetched {len(data)})")
        sleep_fn(polite_sleep)

    papers = sorted(by_id.values(), key=lambda p: p.published_at or "", reverse=True)
    return papers, per_query
