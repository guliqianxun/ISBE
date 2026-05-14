"""One-off probe: fetch 36氪快讯 via Crawl4AI, dump first 4KB of markdown.

Goal: decide whether the page renders enough text statically for our LLM
extractor, or if we need to wait_for a JS selector / scroll.
"""
from __future__ import annotations

import asyncio
import sys

from isbe.topics._shared.crawl4ai_collector import _fetch_markdown


async def main(url: str) -> None:
    md, meta = await _fetch_markdown(url)
    print("=== META ===")
    print(meta)
    print("=== MARKDOWN LEN ===", len(md))
    print("=== HEAD 6000 ===")
    print(md[:6000])
    print("=== TAIL 2000 ===")
    print(md[-2000:])


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://36kr.com/newsflashes"
    asyncio.run(main(url))
