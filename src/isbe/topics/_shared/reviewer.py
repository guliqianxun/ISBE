"""审核员 — RAGAS-style faithfulness pass over the writer's OPINION output.

The fact layer is already mechanically grounded (cards + verbatim tables), so
this stage only audits opinions: TL;DR bullets, per-paper 评价/速览, and the
分析 section. One smart-tier call decomposes them into atomic claims and
judges each against the evidence bundle:

- supported   证据直接支持
- drift       有依据但程度/范围漂移（"未跑赢 persistence" vs "not consistently
              outperform simpler alternatives" 这类）
- unsupported 证据中找不到依据

Any drift/unsupported triggers ONE writer rewrite with the reviewer's notes
appended; the second output is re-audited and shipped regardless (residual
flags land in the audit block + run payload — Elicit-style, the human remains
the final reviewer).
"""

from __future__ import annotations

import json
import re

from isbe.llm.client import complete

_REVIEW_SYSTEM = """你是 ISBE 的审核员。输入是【证据】（论文摘要 + 已验证的证据卡 + 仓库数据）
和一份【报告】。你的任务：把报告中 TL;DR、各论文的「评价/速览」、以及「分析」段里的
**可核查断言**分解为原子 claim，逐条判定：

- "supported": 证据直接支持
- "drift": 有依据，但程度/范围被夸大或缩小（如证据说「未能一致优于简单基线」而报告写「全面落败」）
- "unsupported": 证据中找不到依据（包括对仓库归属/作者/背景的无据断言）

纯观点措辞（"值得细读"、"有新意"）不算 claim——除非其中内嵌了事实断言。
只输出 JSON 数组，不要其他文字：
[{"claim": "...", "verdict": "supported|drift|unsupported", "note": "一句依据说明"}]
没有可核查断言时输出 []。"""


def _strip_json_envelope(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    start, end = t.find("["), t.rfind("]")
    return t[start : end + 1] if start != -1 and end > start else t


def review_claims(*, evidence: str, report: str, complete_fn=None) -> list[dict]:
    """One reviewer call → list of {claim, verdict, note}. Fail-open: an
    unparseable reviewer response audits nothing rather than blocking the
    digest."""
    if complete_fn is None:
        def complete_fn(system: str, user: str) -> str:
            return complete(system=system, user=user, tier="smart").text

    user = f"=== 证据 ===\n{evidence}\n\n=== 报告 ===\n{report}"
    try:
        raw = complete_fn(_REVIEW_SYSTEM, user)
        data = json.loads(_strip_json_envelope(raw))
        if not isinstance(data, list):
            return []
        out = []
        for item in data:
            if (
                isinstance(item, dict)
                and item.get("claim")
                and item.get("verdict") in ("supported", "drift", "unsupported")
            ):
                out.append(
                    {
                        "claim": str(item["claim"])[:300],
                        "verdict": item["verdict"],
                        "note": str(item.get("note", ""))[:300],
                    }
                )
        return out
    except (ValueError, TypeError):
        return []


def _feedback(flagged: list[dict]) -> str:
    lines = [
        "以下断言未通过证据审核，请修正（改写到与证据一致，或删除无据断言），然后重新输出完整六段："
    ]
    for f in flagged:
        lines.append(f"- [{f['verdict']}] {f['claim']} —— {f['note']}")
    return "\n".join(lines)


def review_and_maybe_rewrite(
    *,
    system_prompt: str,
    user_prompt: str,
    first_text: str,
    evidence: str,
    writer_fn=None,
    reviewer_fn=None,
) -> tuple[str, dict]:
    """Audit → (at most one) rewrite → re-audit.

    Returns (final_text, summary) where summary carries telemetry + residual
    flagged claims for the audit block.
    """
    if writer_fn is None:
        def writer_fn(system: str, user: str) -> str:
            return complete(system=system, user=user).text

    verdicts = review_claims(evidence=evidence, report=first_text, complete_fn=reviewer_fn)
    flagged = [v for v in verdicts if v["verdict"] != "supported"]
    summary = {
        "claims": len(verdicts),
        "flagged_first_pass": len(flagged),
        "rewritten": False,
        "flagged_residual": [],
    }
    if not flagged:
        return first_text, summary

    second_user = f"{user_prompt}\n\n=== 审核员意见 ===\n{_feedback(flagged)}"
    second_text = writer_fn(system_prompt, second_user)
    summary["rewritten"] = True

    verdicts2 = review_claims(evidence=evidence, report=second_text, complete_fn=reviewer_fn)
    residual = [v for v in verdicts2 if v["verdict"] != "supported"]
    summary["flagged_residual"] = [
        f"[{v['verdict']}] {v['claim']}" for v in residual[:5]
    ]
    return second_text, summary
