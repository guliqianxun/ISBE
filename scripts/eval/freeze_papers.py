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
from datetime import UTC, datetime
from pathlib import Path

import yaml

from isbe.topics._shared.semantic_scholar import acquire

REPO = Path(__file__).resolve().parents[2]


def _record(p) -> dict:
    """S2Paper → collection.jsonl 行。"""
    return {
        "id": p.id,
        "arxiv_id": p.arxiv_id,
        "source": "semantic-scholar",
        "published_at": p.published_at,
        "headline": p.title,
        "url": p.url,
        "summary": p.abstract,
        "lang": "en",
        "citation_count": p.citation_count,      # RC5
        "fields_of_study": list(p.fields_of_study),  # RC7
        "query_hit": p.query_hit,
    }


def freeze(topic: str, year_from: int, per_query: int) -> None:
    topic_yaml = REPO / "src" / "isbe" / "topics" / topic / "topic.yaml"
    cfg = yaml.safe_load(topic_yaml.read_text(encoding="utf-8"))
    # 优先契约 queries（RC2），退回 arxiv.include_keywords（向后兼容）
    queries = (cfg.get("retrieval") or {}).get("queries") \
        or (cfg.get("arxiv") or {}).get("include_keywords") \
        or [cfg.get("label", topic)]

    year = datetime.now(UTC).year
    papers, per_q = acquire(
        queries, year_from=year_from, year_to=year,
        limit_per_query=per_query, log=print,
    )
    items = [_record(p) for p in papers]
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
    print(f"  qrels tmpl: tests/eval/{topic}/{snapshot}/qrels.template.jsonl (fill rel/facet -> qrels.jsonl)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("topic", nargs="?", default="video-generation")
    ap.add_argument("--year-from", type=int, default=2025)
    ap.add_argument("--limit", type=int, default=30, help="per-query result cap")
    args = ap.parse_args()
    freeze(args.topic, args.year_from, args.limit)
