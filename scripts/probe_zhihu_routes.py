"""Smoke test for RSSHub Zhihu routes.

Run after pasting fresh ZHIHU_COOKIES into .env (need both `d_c0=...; z_c0=...`).
Expected post-fix: all routes return 200 OK with non-empty `<item>` list.

Usage:
    docker compose up -d rsshub          # ensure rsshub picks up new env
    uv run python scripts/probe_zhihu_routes.py
"""
from __future__ import annotations

import re
import sys

import httpx

ROUTES = [
    ("daily (no cookies needed)", "/zhihu/daily"),
    ("topic intro (motorcycle 19551770)", "/zhihu/topic/19551770/top-answers"),
    ("topic hot", "/zhihu/topic/19551770/hot"),
    ("zhuanlan example column", "/zhihu/zhuanlan/qichefuzhu"),
    ("user answers (zhang-jia-wei)", "/zhihu/people/answers/zhang-jia-wei"),
]


def main() -> int:
    fail = 0
    base = "http://127.0.0.1:1200"
    # trust_env=False: we hit a localhost rsshub; skip env proxy parsing
    # (this machine's NO_PROXY contains `[::1]` which httpx fails to urlparse)
    with httpx.Client(timeout=30.0, trust_env=False) as c:
        for label, path in ROUTES:
            try:
                r = c.get(f"{base}{path}")
            except Exception as e:
                print(f"[ERR ] {label:42s} {path} → {e!s}")
                fail += 1
                continue
            ok = r.status_code == 200
            n_items = len(re.findall(r"<item>", r.text))
            mark = "OK  " if ok and n_items > 0 else "FAIL"
            extra = ""
            if not ok:
                m = re.search(
                    r"details whitespace-pre-line\">([^<]+)", r.text
                )
                if m:
                    extra = f" — {m.group(1)[:120]}"
            print(f"[{mark}] {label:42s} {path} → {r.status_code}, items={n_items}{extra}")
            if not (ok and n_items > 0):
                fail += 1
    return fail


if __name__ == "__main__":
    sys.exit(main())
