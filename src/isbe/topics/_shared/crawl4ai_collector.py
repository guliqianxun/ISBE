"""Crawl4AI-driven collector for sites with no usable RSS (JS-rendered, anti-bot).

Reads `topic.yaml`'s `crawl4ai:` block:

    crawl4ai:
      pages:
        - name: zhihu_topic_motorcycle
          url: https://www.zhihu.com/topic/19551770/top-answers
        - name: zhihu_kol_xxx
          url: https://www.zhihu.com/people/<id>/answers
      lookback_days: 14
      lang: zh

Each page is fetched with a headless Chromium (executes JS), markdown-ified,
then handed to the LLM with an extraction prompt that returns a JSON array of
articles. Each entry becomes one row in `articles`.

Cost note: ~10-30K input tokens + ~500-2K output per page (DeepSeek ≈ $0.001-0.003).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta

from prefect import flow

from isbe.facts.articles import Article
from isbe.facts.db import make_session_factory
from isbe.llm.client import complete
from isbe.observability.runs import topic_run
from isbe.topics.registry import default_topics_root, load_topic_config

_DEFAULT_LOOKBACK_DAYS = 14

_EXTRACT_SYSTEM = """你是一个内容抽取助手。从 markdown 化的网页里识别出文章/回答/帖子列表。

只输出一个 JSON 数组，每元素严格如下结构：
{
  "title": "...",
  "url": "https://...（必须绝对 URL）",
  "author": "...（找不到写 null）",
  "published_at": "YYYY-MM-DD（找不到写 null）",
  "summary": "...（前 200 字预览，找不到写 null）"
}

仅输出 JSON 数组本身，不要 markdown 代码块、不要解释文字。如果页面没有文章列表，输出 `[]`。
"""

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _article_id(source: str, url: str) -> str:
    return hashlib.sha1(f"{source}|{url}".encode()).hexdigest()


def _parse_when(s: str | None) -> datetime:
    if not s:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, TypeError):
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=UTC)
        except (ValueError, TypeError):
            return datetime.now(UTC)


def _strip_json_envelope(text: str) -> str:
    """LLM sometimes wraps JSON in ```json ... ``` despite instructions."""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\[.*\])\s*```", t, re.DOTALL)
    if m:
        return m.group(1)
    return t


async def _fetch_markdown(url: str, *, cookies: list[dict] | None = None) -> tuple[str, dict]:
    """Headless-Chromium fetch → page markdown.

    Uses OS Chrome via `chrome_channel='chrome'` so we don't depend on Playwright's
    bundled-chromium download (which is often firewalled). `cookies` is an optional
    list of `{name, value, domain, path}` dicts to seed session state.
    """
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

    browser_cfg = BrowserConfig(
        headless=True,
        chrome_channel="chrome",
        user_agent=_USER_AGENT,
        cookies=cookies or [],
        verbose=False,
    )
    run_cfg = CrawlerRunConfig(page_timeout=45000, wait_until="networkidle")
    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        result = await crawler.arun(url=url, config=run_cfg)
        if not result.success:
            raise RuntimeError(f"crawl4ai failed: {result.error_message}")
        md = (
            result.markdown
            if isinstance(result.markdown, str)
            else (result.markdown.raw_markdown if result.markdown else "")
        )
        return md, {"status_code": result.status_code, "url_final": result.url}


def _extract_articles(page_markdown: str, *, source_url: str) -> list[dict]:
    """LLM-driven extraction → JSON list. Returns parsed list or []."""
    # cap page size to keep prompt size sane
    body = page_markdown[:40000]
    user = (
        f"Page URL: {source_url}\n\n"
        f"=== PAGE MARKDOWN ===\n{body}\n\n"
        "请按 system 指令输出 JSON 数组。"
    )
    resp = complete(system=_EXTRACT_SYSTEM, user=user)
    raw = _strip_json_envelope(resp.text or "")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return parsed


def _to_article(
    entry: dict, *, topic_id: str, source: str, lang: str
) -> Article | None:
    url = (entry.get("url") or "").strip()
    title = (entry.get("title") or "").strip()
    if not url or not title or not url.startswith(("http://", "https://")):
        return None
    return Article(
        id=_article_id(source, url),
        topic_id=topic_id,
        source=source,
        published_at=_parse_when(entry.get("published_at")),
        headline=title[:1024],
        url=url[:2048],
        summary=(entry.get("summary") or "")[:8000] or None,
        tags=[],
        lang=lang,
    )


def _upsert(session, articles: list[Article]) -> int:
    n = 0
    for a in articles:
        if session.get(Article, a.id) is None:
            session.add(a)
            n += 1
    session.commit()
    return n


def _cookies_for_domain(domain: str) -> list[dict]:
    """Build a Playwright cookies list for `.<domain>` from ISBE env vars."""
    out: list[dict] = []
    if domain.endswith("zhihu.com"):
        z = os.getenv("ZHIHU_COOKIES", "").strip()
        if z:
            out.append({"name": "z_c0", "value": z, "domain": ".zhihu.com", "path": "/"})
    if domain.endswith("weibo.com"):
        w = os.getenv("WEIBO_COOKIES", "").strip()
        if w:
            # WEIBO_COOKIES expected as full `k1=v1; k2=v2;` header — split & emit.
            for part in [p.strip() for p in w.split(";") if "=" in p]:
                k, _, v = part.partition("=")
                out.append(
                    {"name": k.strip(), "value": v.strip(), "domain": ".weibo.com", "path": "/"}
                )
    return out


def _domain_of(url: str) -> str:
    # cheap parse; sufficient for cookie lookup
    return url.split("://", 1)[-1].split("/", 1)[0]


@flow(name="crawl4ai-collector")
def crawl4ai_collector(topic_id: str) -> int:
    """Crawl each `crawl4ai.pages` URL with headless Chromium + LLM extraction."""
    cfg = load_topic_config(default_topics_root(), topic_id)
    ccfg = cfg.get("crawl4ai") or {}
    pages = ccfg.get("pages") or []
    if not pages:
        raise ValueError(f"topic {topic_id}: no `crawl4ai.pages:` configured")
    lookback_days = int(ccfg.get("lookback_days", _DEFAULT_LOOKBACK_DAYS))
    lang = ccfg.get("lang", "zh")
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

    with topic_run(topic_id, "crawl4ai-collector") as run:
        per_page: dict[str, int] = {}
        errors: list[str] = []
        new_total = 0
        for p in pages:
            name = p["name"]
            url = p["url"]
            try:
                md, _meta = asyncio.run(
                    _fetch_markdown(url, cookies=_cookies_for_domain(_domain_of(url)))
                )
            except Exception as e:
                errors.append(f"{name}: fetch — {e!s}")
                per_page[name] = -1
                continue
            try:
                entries = _extract_articles(md, source_url=url)
            except Exception as e:
                errors.append(f"{name}: extract — {e!s}")
                per_page[name] = -1
                continue
            kept: list[Article] = []
            for entry in entries:
                art = _to_article(entry, topic_id=topic_id, source=name, lang=lang)
                if art is None:
                    continue
                if art.published_at < cutoff:
                    continue
                kept.append(art)
            Session = make_session_factory()
            with Session() as s:
                added = _upsert(s, kept)
            per_page[name] = added
            new_total += added
        run.payload["new_articles"] = new_total
        run.payload["per_page"] = per_page
        if errors:
            run.payload["errors"] = errors
        return new_total
