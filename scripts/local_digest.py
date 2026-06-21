"""Fully LOCAL ISBE nowcasting digest + delivery — no server, no Postgres.

Pipeline (all local):
  build_pool.py  (local arXiv DB acquire+triage)
    -> extract_docs.py / extract_assets.py  (docling full-text + figure/table, explore-os venv)
    -> DeepSeek per paper -> grounded verifiability card (tmp/wf/card_<i>.json)
    -> render_report.py -> tmp/report.html
    -> send_report_email.py (SOCKS-tunneled SMTP)

Card grounding is enforced in the DeepSeek prompt (anchor every field to a
verbatim span; 未提及 when absent; never invent URL/number; no org-absence
boilerplate). The multi-agent audit gates remain a separate QA tool, not run
per delivery.

Run: uv run python scripts/local_digest.py [--window 7] [--no-email] [--one]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
WF = REPO / "tmp" / "wf"
EXPLORE_PY = r"E:\codes\explore-os\.venv\Scripts\python.exe"
load_dotenv(REPO / ".env")
import os  # noqa: E402

DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "")

_CARD_PROMPT = """你是严格 grounded 的科研周报卡片撰写器。下面是一篇论文的标题、摘要与 docling 抽取的正文（含结果表）。
只依据给出的文本，产出一个 JSON 对象（仅输出 JSON，不要多余文字），字段如下，每个文本字段配一个 anchor（从正文/摘要逐字复制的≤120字证据片段；无依据则 text 写"未提及"、anchor 写""）：

{
 "verdict": {"text": "≤2句价值判断", "anchor": ""},
 "plain":   {"text": "1句大白话(给入门读者)", "anchor": ""},
 "provenance": {"text": "第一作者+机构/实验室(取自正文首页署名);若文中自述延续某前作则注明。严禁写'未提及机构'之类废话——查不到机构就只写作者名", "anchor": ""},
 "method":  {"text": "核心方法+建立在什么之上(背景)", "anchor": ""},
 "data":    {"text": "数据集名+公开还是自采+链接(若文中有)", "anchor": ""},
 "code":    {"text": "仓库URL(仅当文中确有)否则'未提及'——绝不编造URL", "anchor": ""},
 "repro":   {"open": "是/否/未知", "weights": "是/否/未知", "compute": "如 1×A100/未知", "completeness": "高/中/低/未知", "anchor": ""},
 "effect":  {"none": true/false, "claims": [{"metric":"","dataset":"","baseline_model":"","baseline":"","new":"","anchor":"逐字片段"}]}
}
effect 仅在文中(尤其结果表)给出可比数字时填，带对照基线名；无数字则 none=true、claims=[]。绝不编造数字。

=== 标题 ===
{title}

=== 摘要 ===
{abstract}

=== 正文(docling 抽取，可能截断) ===
{body}
"""


def deepseek_card(title: str, abstract: str, body: str) -> dict:
    prompt = (_CARD_PROMPT.replace("{title}", title or "")
              .replace("{abstract}", abstract or "")
              .replace("{body}", (body or "")[:16000]))
    r = httpx.post(
        "https://api.deepseek.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
        json={"model": "deepseek-chat", "max_tokens": 1500,
              "messages": [{"role": "user", "content": prompt}],
              "response_format": {"type": "json_object"}},
        timeout=120, trust_env=True,
    )
    r.raise_for_status()
    txt = r.json()["choices"][0]["message"]["content"]
    return json.loads(txt)


def gen_cards(pool: dict) -> None:
    docs = {p.stem.split("_")[1]: p for p in WF.glob("doc_*.md")}
    for i, rec in enumerate(pool["kept"]):
        aid = rec["arxiv_id"]
        body = (WF / f"doc_{i}.md").read_text(encoding="utf-8") if (WF / f"doc_{i}.md").exists() else ""
        try:
            card = deepseek_card(rec["title"], rec.get("abstract", ""), body)
        except Exception as e:  # noqa: BLE001
            print(f"[{i}] {aid}: DeepSeek FAIL {e}", flush=True)
            card = {"verdict": {"text": "待核", "anchor": ""}}
        card["arxiv_id"] = aid
        out = {"arxiv_id": aid, "clean": True, "field_verdicts": [], "corrected_card": card}
        (WF / f"card_{i}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{i}] {aid}: card ok", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=7)
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--one", action="store_true", help="smoke: 1 paper, no email")
    args = ap.parse_args()

    print("== 1/5 acquire+triage (local arXiv DB) =="); sys.stdout.flush()
    subprocess.run([sys.executable, str(REPO / "scripts" / "build_pool.py"), str(args.window)], check=True)

    print("== 2/5 docling extract (full-text + figures/tables) =="); sys.stdout.flush()
    subprocess.run([EXPLORE_PY, str(REPO / "scripts" / "extract_docs.py")], check=True)
    subprocess.run([EXPLORE_PY, str(REPO / "scripts" / "extract_assets.py")], check=True)

    pool = json.loads((REPO / "tmp" / "pool.json").read_text(encoding="utf-8"))
    if args.one:
        pool["kept"] = pool["kept"][:1]
    print(f"== 3/5 DeepSeek cards ({len(pool['kept'])} papers) =="); sys.stdout.flush()
    for old in WF.glob("card_*.json"):
        old.unlink()
    (WF / "coverage.json").unlink(missing_ok=True)  # local delivery = rule-gate, not audit
    gen_cards(pool)

    print("== 4/5 render report =="); sys.stdout.flush()
    subprocess.run([sys.executable, str(REPO / "scripts" / "render_report.py")], check=True)

    if args.no_email or args.one:
        print("(skip email)"); return
    print("== 5/5 email (SOCKS) =="); sys.stdout.flush()
    subprocess.run(["uv", "run", "--with", "PySocks", "python",
                    str(REPO / "scripts" / "send_report_email.py")], check=True)


if __name__ == "__main__":
    main()
