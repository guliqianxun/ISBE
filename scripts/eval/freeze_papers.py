"""冻结某科研 topic 的论文采集集为 eval fixture（研究型检索能力集 §4-§6）。

为什么用 Semantic Scholar 而非 arxiv API：
  本机（CN + 共享代理出口）直连 export.arxiv.org 被 WAF 硬封（429 / 断连，实测）；
  arxiv API 仅服务器侧可达。Semantic Scholar graph API 退避后可达，且额外给
  citationCount / fieldsOfStudy / externalIds.ArXiv —— 正好喂 RC5 显著性 / RC7 归类
  / RC6 谱系，并用 arxiv_id 桥回现有 papers 管线。

策略：把 topic.yaml 的每个 include_keyword 当一条 S2 查询，结果并集 —— 天然构成
比当前单一窄查询更宽的**池**（RC2/RC3 召回评估用）。只做去重 + 时间窗，不做相关性过滤
（快照取在过滤前，使 qrels 能判定全集）。

输出 tests/eval/<topic>/<snapshot>/：collection.jsonl + qrels.template.jsonl

用法：
  uv run python scripts/eval/freeze_papers.py video-generation
  uv run python scripts/eval/freeze_papers.py video-generation --year-from 2025 --limit 40
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parents[2]
UA = {"User-Agent": "ISBE-eval-freeze/0.1 (mailto:visitorindark@gmail.com)"}
S2 = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "title,abstract,year,publicationDate,citationCount,externalIds,fieldsOfStudy,authors"


def _get(url: str, tries: int = 7) -> dict | None:
    """S2 未授权限速重，退避重试清 429。"""
    for i in range(tries):
        try:
            r = httpx.get(url, headers=UA, timeout=45.0)
            if r.status_code == 200:
                return r.json()
            print(f"    (try {i + 1}: HTTP {r.status_code}, backoff)")
        except Exception as ex:  # noqa: BLE001
            print(f"    (try {i + 1}: {type(ex).__name__}, backoff)")
        time.sleep(4 * (i + 1))
    return None


def freeze(topic: str, year_from: int, per_query: int) -> None:
    topic_yaml = REPO / "src" / "isbe" / "topics" / topic / "topic.yaml"
    cfg = yaml.safe_load(topic_yaml.read_text(encoding="utf-8"))
    acfg = cfg.get("arxiv") or {}
    queries = acfg.get("include_keywords") or [cfg.get("label", topic)]

    year = datetime.now(UTC).year
    by_id: dict[str, dict] = {}
    per_q: dict[str, int] = {}
    for q in queries:
        url = (
            f"{S2}?query={httpx.QueryParams({'q': q})['q']}"
            f"&year={year_from}-{year}&limit={per_query}&fields={FIELDS}"
        )
        d = _get(url)
        if not d:
            print(f"  query '{q}': FAILED after backoff")
            per_q[q] = -1
            continue
        data = d.get("data") or []
        added = 0
        for p in data:
            ext = p.get("externalIds") or {}
            arxiv_id = ext.get("ArXiv")
            key = arxiv_id or p.get("paperId")
            if not key or key in by_id:
                continue
            by_id[key] = {
                "id": arxiv_id or f"s2:{p.get('paperId')}",
                "arxiv_id": arxiv_id,
                "source": "semantic-scholar",
                "published_at": p.get("publicationDate") or (f"{p.get('year')}-01-01" if p.get("year") else None),
                "headline": (p.get("title") or "").strip(),
                "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else f"https://www.semanticscholar.org/paper/{p.get('paperId')}",
                "summary": (p.get("abstract") or "").strip() or None,
                "lang": "en",
                # extras for RC5/RC6/RC7
                "citation_count": p.get("citationCount"),
                "fields_of_study": p.get("fieldsOfStudy") or [],
                "query_hit": q,
            }
            added += 1
        per_q[q] = added
        print(f"  query '{q}': +{added} new (fetched {len(data)})")
        time.sleep(4)  # 礼貌间隔

    items = sorted(by_id.values(), key=lambda r: (r["published_at"] or ""), reverse=True)
    iso = datetime.now(UTC).isocalendar()
    snapshot = f"{iso.year}-W{iso.week:02d}"
    out_dir = REPO / "tests" / "eval" / topic / snapshot
    out_dir.mkdir(parents=True, exist_ok=True)

    with (out_dir / "collection.jsonl").open("w", encoding="utf-8") as f:
        for r in items:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (out_dir / "qrels.template.jsonl").open("w", encoding="utf-8") as f:
        for r in items:
            f.write(json.dumps({
                "item_id": r["id"],
                "rel": None,        # 0 not-relevant / 1 relevant / 2 high-value
                "facet": None,      # RC7 子主题面（待你重列后填）
                "must_hit": False,
                "_title": r["headline"],
                "_arxiv": r["arxiv_id"],
                "_cite": r["citation_count"],
            }, ensure_ascii=False) + "\n")

    arxiv_n = sum(1 for r in items if r["arxiv_id"])
    print(f"snapshot {snapshot}: {len(items)} papers ({arxiv_n} with arxiv_id)  per_query={per_q}")
    print(f"  collection: tests/eval/{topic}/{snapshot}/collection.jsonl")
    print(f"  qrels tmpl: tests/eval/{topic}/{snapshot}/qrels.template.jsonl  (fill rel/facet, save as qrels.jsonl)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("topic", nargs="?", default="video-generation")
    ap.add_argument("--year-from", type=int, default=2025)
    ap.add_argument("--limit", type=int, default=30, help="per-query result cap")
    args = ap.parse_args()
    freeze(args.topic, args.year_from, args.limit)
