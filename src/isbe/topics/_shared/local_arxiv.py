"""本地 arxiv 源 adapter —— 读外部每日拉取项目的 papers.db（FTS5 全文检索）。

为什么用它而非 S2：本地 SQLite，**当天新鲜**（每日 harvest）、**无限速**、**无 WAF**、
FTS5 即时全文检索；cs.CV 完美覆盖 video-generation。附带 hf_upvotes（HuggingFace
社区热度）——新论文的显著性信号，远胜数月滞后的 citationCount。

只读打开（immutable），不写不锁；返回与 S2 同形的 S2Paper，pipeline 零改复用。
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from datetime import date, timedelta

from isbe.topics._shared.semantic_scholar import S2Paper

DEFAULT_DB = r"I:\essaies\archive\arxiv-cs.CV\papers.db"


def _fts_query(q: str) -> str:
    """把契约 query 转成安全的 FTS5 表达式：各 token 加引号、隐式 AND（宽召回）。

    避免连字符/冒号等特殊字符让 FTS5 解析报错（text-to-video → "text" "to" "video"）。
    """
    toks = re.findall(r"[A-Za-z0-9]+", q)
    return " ".join(f'"{t}"' for t in toks)


def _row_to_paper(row: tuple, query_hit: str) -> S2Paper:
    pid, title, abstract, update_date, hf_upvotes = row
    return S2Paper(
        id=pid,
        arxiv_id=pid,
        title=(title or "").strip(),
        abstract=(abstract or "").strip() or None,
        published_at=update_date,
        url=f"https://arxiv.org/abs/{pid}",
        # 暂不把 hf_upvotes 塞进 citation_count（语义不同）；留作后续 fresh-significance 信号。
        citation_count=None,
        fields_of_study=("Computer Science",),
        query_hit=query_hit,
    )


def acquire_local(
    queries: list[str],
    *,
    reference_date: date,
    since_days: int,
    limit_per_query: int,
    db_path: str = DEFAULT_DB,
    log: Callable[[str], None] = lambda _m: None,
) -> tuple[list[S2Paper], dict[str, int]]:
    """近窗 FTS 多查询并集。update_date >= reference_date - since_days。

    返回 (papers 按 update_date 降序, per_query)。无网络、无限速——本地即时。
    """
    cutoff = (reference_date - timedelta(days=since_days)).isoformat()
    con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    sql = (
        "select p.id, p.title, p.abstract, p.update_date, p.hf_upvotes "
        "from papers p join papers_fts f on p.rowid = f.rowid "
        "where papers_fts match ? and p.update_date >= ? "
        "order by p.update_date desc limit ?"
    )
    by_id: dict[str, S2Paper] = {}
    per_query: dict[str, int] = {}
    try:
        for q in queries:
            try:
                rows = con.execute(sql, (_fts_query(q), cutoff, limit_per_query)).fetchall()
            except sqlite3.OperationalError as e:  # 畸形 FTS 表达式等
                per_query[q] = -1
                log(f"  query {q!r}: FTS error {e}")
                continue
            added = 0
            for r in rows:
                if r[0] in by_id:
                    continue
                by_id[r[0]] = _row_to_paper(r, q)
                added += 1
            per_query[q] = added
            log(f"  query {q!r}: +{added} new (fetched {len(rows)})")
    finally:
        con.close()

    papers = sorted(by_id.values(), key=lambda p: p.published_at or "", reverse=True)
    return papers, per_query
