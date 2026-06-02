"""冻结某 topic 当前 RSS 采集集为 eval fixture（设计 §4.1）。

抓取 topic.yaml 配置的 RSS 源，解析为 Item，**只做去重 + lookback + exclude_url_patterns**，
刻意**不做 exclude_keywords 相关性过滤**——快照取在过滤前，使 qrels 能判定全集、
triage 层才能接管并被测量。

输出（写到 tests/eval/<topic>/<snapshot>/）：
  collection.jsonl       冻结的采集集（triage 的输入 + 标注对象）
  qrels.template.jsonl   每条一行，rel 留空待人工填（这是 ground truth，必须你来标）

用法：
  uv run python scripts/eval/freeze_collection.py motorcycle
  uv run python scripts/eval/freeze_collection.py motorcycle --lookback 14
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import feedparser
import httpx
import yaml

_UA = "ISBE-eval-freeze/0.1"
REPO = Path(__file__).resolve().parents[2]


def _article_id(source: str, url: str) -> str:
    return hashlib.sha1(f"{source}|{url}".encode()).hexdigest()


def _parse_when(entry: dict) -> datetime:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=UTC)
    return datetime.now(UTC)


def freeze(topic: str, lookback: int | None) -> None:
    topic_yaml = REPO / "src" / "isbe" / "topics" / topic / "topic.yaml"
    cfg = yaml.safe_load(topic_yaml.read_text(encoding="utf-8"))
    rcfg = cfg.get("rss") or {}
    feeds = rcfg.get("feeds") or []
    if not feeds:
        raise SystemExit(f"topic {topic}: no rss.feeds configured")
    lookback_days = lookback if lookback is not None else int(rcfg.get("lookback_days", 14))
    exclude_url_patterns = rcfg.get("exclude_url_patterns") or []
    lang = rcfg.get("lang", "en")
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

    seen: set[str] = set()
    items: list[dict] = []
    per_feed: dict[str, int] = {}
    for feed in feeds:
        name, url = feed["name"], feed["url"]
        try:
            resp = httpx.get(url, headers={"User-Agent": _UA}, timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
            entries = feedparser.parse(resp.text).entries
        except Exception as e:  # noqa: BLE001
            print(f"  {name}: FETCH FAIL {e!r}")
            per_feed[name] = -1
            continue
        kept = 0
        for e in entries:
            link = (e.get("link") or "").strip()
            headline = (e.get("title") or "").strip()
            if not link or not headline:
                continue
            when = _parse_when(e)
            if when < cutoff:
                continue
            if exclude_url_patterns and any(p in link for p in exclude_url_patterns):
                continue
            iid = _article_id(name, link)
            if iid in seen:
                continue
            seen.add(iid)
            summary = (e.get("summary") or e.get("description") or "").strip()[:8000]
            items.append({
                "id": iid,
                "source": name,
                "published_at": when.isoformat(),
                "headline": headline[:1024],
                "url": link[:2048],
                "summary": summary or None,
                "lang": lang,
            })
            kept += 1
        per_feed[name] = kept

    items.sort(key=lambda r: r["published_at"], reverse=True)

    iso = datetime.now(UTC).isocalendar()
    snapshot = f"{iso.year}-W{iso.week:02d}"
    out_dir = REPO / "tests" / "eval" / topic / snapshot
    out_dir.mkdir(parents=True, exist_ok=True)

    coll = out_dir / "collection.jsonl"
    with coll.open("w", encoding="utf-8") as f:
        for r in items:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    qrels = out_dir / "qrels.template.jsonl"
    with qrels.open("w", encoding="utf-8") as f:
        for r in items:
            f.write(json.dumps({
                "item_id": r["id"],
                "rel": None,          # 待标：0 不相关 / 1 相关 / 2 高价值
                "must_hit": False,    # 待标：本期必须命中的锚点设 true
                "_headline": r["headline"],   # 标注辅助（评估时忽略下划线字段）
                "_source": r["source"],
                "_url": r["url"],
            }, ensure_ascii=False) + "\n")

    print(f"snapshot {snapshot}: {len(items)} items  per_feed={per_feed}")
    print(f"  collection: {coll.relative_to(REPO)}")
    print(f"  qrels tmpl: {qrels.relative_to(REPO)}  (fill rel, save as qrels.jsonl)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("topic")
    ap.add_argument("--lookback", type=int, default=None)
    args = ap.parse_args()
    freeze(args.topic, args.lookback)
