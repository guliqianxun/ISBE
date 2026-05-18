"""Fast-tier per-article review pre-pass.

When facts > ~20, the smart LLM gives up on per-article evaluation —
the synthesis call produces good TL;DR + analysis but `## 文章逐条` ends up
empty and every row in the template renders `—`. We split the work:

  pre-pass: batches of ~25 articles, fast/cheap tier, model only writes
            `- [id=xxxxxxxxxxxx]: <one-line eval>` lines (no other output)
  synthesis: existing smart-tier 5-section prompt; receives facts_block with
             reviews already inlined so it doesn't redo the per-item work

This module owns the pre-pass. Synthesis stays in articles_digester.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from isbe.topics._shared.digester_utils import parse_bracketed_reviews

REVIEWS_SYSTEM_PROMPT = (
    "你是一名严谨的资讯评审。对每一条给定的文章，写 1-2 行的精炼评价：是否有价值、为什么、关键论点。\n"
    "严格按格式输出，每行一条，文章顺序与输入一致：\n"
    "- [id=<12位id前缀>]: <你的评价文字>\n"
    "不要写任何前言、总结、Markdown 标题或其它额外内容。只输出 `- [id=...]:` 行。"
)


def build_reviews_prompt(*, topic_label: str, articles: Iterable[Any]) -> str:
    """Build the user prompt for one batch of articles in the pre-pass.

    Each article appears as a compact bullet keyed by its 12-char id prefix —
    same key shape parse_bracketed_reviews expects on the way back.
    """
    lines: list[str] = [f"主题：{topic_label}", "", "请对下列文章每篇写一行评价："]
    for a in articles:
        snippet = (a.summary or "").replace("\n", " ")[:240]
        date_str = (
            a.published_at.strftime("%Y-%m-%d")
            if hasattr(a.published_at, "strftime")
            else str(a.published_at)
        )
        lines.append(
            f"- [id={a.id[:12]}] [{a.source}] {date_str} {a.headline}\n  {snippet}"
        )
    return "\n".join(lines)


def _chunks(seq: list, size: int) -> list[list]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def collect_article_reviews(
    articles: list,
    *,
    topic_label: str,
    complete_fn: Callable[..., Any],
    batch_size: int = 25,
) -> dict[str, str]:
    """Run the fast-tier pre-pass over `articles` in batches.

    Returns `{full_article_id: review_text}`. Articles whose batch failed or
    whose review wasn't returned simply don't appear in the dict — the
    caller handles missing entries gracefully (template renders `—`).
    """
    out: dict[str, str] = {}
    for batch in _chunks(list(articles), batch_size):
        user = build_reviews_prompt(topic_label=topic_label, articles=batch)
        try:
            resp = complete_fn(
                system=REVIEWS_SYSTEM_PROMPT,
                user=user,
                tier="fast",
            )
        except Exception as e:  # noqa: BLE001 — pre-pass is best-effort per batch
            # Partial results matter more than one bad call.
            print(f"[article_reviews] batch failed ({len(batch)} items): {e}")
            continue
        raw = parse_bracketed_reviews(resp.text or "")
        # Map 12-char prefix keys back to full ids
        for a in batch:
            key12 = a.id[:12]
            if key12 in raw:
                out[a.id] = raw[key12]
    return out
