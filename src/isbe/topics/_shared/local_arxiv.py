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
from isbe.triage.contract import RetrievalContract

DEFAULT_DB = r"I:\essaies\archive\arxiv-cs.CV\papers.db"


def _phrase(term: str) -> str:
    """单个词条 → FTS5 引号短语（相邻匹配）。特殊字符(连字符/冒号/斜杠)安全化。

    "precipitation forecast" → '"precipitation forecast"'；text-to-video → '"text to video"'。
    """
    toks = re.findall(r"[A-Za-z0-9]+", term)
    return '"' + " ".join(toks) + '"' if toks else ""


def _net_query(contract: RetrievalContract) -> str:
    """领域宽召回网：queries ∪ entity_terms ∪ require_any 各作短语，OR 连接。

    "撒大网、相关性留给 triage"——本地 FTS 用领域词汇 OR 把近窗里沾边的全捞来，
    再交 out_of_scope + require_any 门 + 分层精筛。比把多词 query 当 token-AND 宽得多。
    """
    seen: set[str] = set()
    phrases: list[str] = []
    for term in (*contract.queries, *contract.entity_terms, *contract.require_any):
        p = _phrase(term)
        if p and p.lower() not in seen:
            seen.add(p.lower())
            phrases.append(p)
    return " OR ".join(phrases)


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
    contract: RetrievalContract,
    *,
    reference_date: date,
    since_days: int,
    limit: int = 500,
    db_path: str = DEFAULT_DB,
    log: Callable[[str], None] = lambda _m: None,
) -> tuple[list[S2Paper], dict[str, int]]:
    """近窗领域宽召回（单 OR 网查询）。update_date >= reference_date - since_days。

    返回 (papers 按 update_date 降序, 计数)。无网络、无限速——本地即时。
    宽召回构池、相关性交 triage：用 _net_query 把近窗沾边的全捞来。
    """
    net = _net_query(contract)
    if not net:
        return [], {}
    cutoff = (reference_date - timedelta(days=since_days)).isoformat()
    con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    sql = (
        "select p.id, p.title, p.abstract, p.update_date, p.hf_upvotes "
        "from papers p join papers_fts f on p.rowid = f.rowid "
        "where papers_fts match ? and p.update_date >= ? "
        "order by p.update_date desc limit ?"
    )
    try:
        rows = con.execute(sql, (net, cutoff, limit)).fetchall()
    finally:
        con.close()

    by_id: dict[str, S2Paper] = {}
    for r in rows:
        if r[0] not in by_id:
            by_id[r[0]] = _row_to_paper(r, "local-net")
    papers = sorted(by_id.values(), key=lambda p: p.published_at or "", reverse=True)
    log(f"  local-net: {len(papers)} 篇 (近 {since_days} 天, since {cutoff})")
    return papers, {"local-net": len(papers)}
