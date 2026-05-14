"""Smoke test for morerssplz Zhihu 专栏 routes.

morerssplz (lilydjwg/morerssplz) is the no-cookie fallback for Zhihu /zhuanlan
columns. It scrapes the HTML directly, so it sidesteps RSSHub's x-zse-96
signature wall — at the cost of covering ONLY /zhuanlan (no topic/people).

Route shape: GET /zhihuzhuanlan/<column-slug>
                       ^ note: no slash between "zhihu" and "zhuanlan"

Usage:
    docker compose up -d morerssplz
    uv run python scripts/probe_morerssplz.py
"""
from __future__ import annotations

import re
import sys

import httpx

# AI-leaning seed columns (verified active 2026-05-15). User can add more by
# editing this list or src/isbe/topics/china_tech/topic.yaml. Note: a slug can
# return 200 with stale items (e.g. `aiera` last posted 2025-10) which then
# get filtered out by `lookback_days`. Spot-check pubDate before adding.
ROUTES = [
    ("机器之心 (research summaries)", "/zhihuzhuanlan/jiqizhixin"),
    ("量子位 (applied AI/products)", "/zhihuzhuanlan/qbitai"),
]


def main() -> int:
    fail = 0
    base = "http://127.0.0.1:1201"
    # trust_env=False: localhost target; skip env proxy parsing (this machine's
    # NO_PROXY contains `[::1]` which httpx fails to urlparse).
    with httpx.Client(timeout=30.0, trust_env=False) as c:
        for label, path in ROUTES:
            try:
                r = c.get(f"{base}{path}")
            except Exception as e:
                print(f"[ERR ] {label:38s} {path} → {e!s}")
                fail += 1
                continue
            n_items = len(re.findall(r"<item>", r.text))
            ok = r.status_code == 200 and n_items > 0
            mark = "OK  " if ok else "FAIL"
            print(f"[{mark}] {label:38s} {path} → {r.status_code}, items={n_items}")
            if not ok:
                fail += 1
    return fail


if __name__ == "__main__":
    sys.exit(main())
