"""Monthly digester — rollup of the month's weekly digests + thesis evolution.

Weekly reports answer "过去 7 天发生了什么"; the monthly answers "这个月的
脉络是什么、我的论点怎么演化了". Inputs are all local: the month's weekly
artifacts from the mirror (data-URI figures stripped, per-week char cap) and
the current memory snapshot. One smart-tier synthesis call produces:

    月度总览 / 本月必读 / 论点演化 / 下月关注 / 蒸馏

Distillation drafts go through the same .pending review flow as weeklies.
ISO weeks belong to the month containing their Thursday (ISO 8601 rule), so
a week straddling two months lands in exactly one monthly report.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from jinja2 import Template
from prefect import flow

from isbe.artifacts.store import save_artifact
from isbe.llm.client import complete
from isbe.memory.pending import write_pending
from isbe.notify import send_digest_notification
from isbe.observability.runs import topic_run
from isbe.topics._shared.digester_utils import (
    build_memory_block,
    parse_distillation_section,
)
from isbe.topics._shared.digester_utils import (
    memory_root as _memory_root,
)
from isbe.topics.base import DigestResult, DigestSection
from isbe.topics.registry import default_topics_root, load_topic_config

MONTHLY_TEMPLATE = Path(__file__).parent / "templates" / "monthly.j2"

_DATAURI_RE = re.compile(r"data:image/\w+;base64,[A-Za-z0-9+/=]+")
_PER_WEEK_CHAR_CAP = 18_000

MONTHLY_SYSTEM_PROMPT = """你是 ISBE 的月报助手。输入是本月各期周报全文与当前 memory。
输出严格分五段，用 markdown level-2 标题分隔（顺序固定）：

## 月度总览
3-5 个 bullet：本月该领域的主线进展与信号（跨周的脉络，不是周报复读）。

## 本月必读
从各周报的论文中挑 3-5 篇本月最值得读的，每篇一行：
`- [<arxiv_id>] <标题或简称> — <为什么入选，≤40 字>`
只能挑周报中真实出现过的论文，禁止编造。

## 论点演化
对照 memory：本月的证据加强了哪些论点、削弱了哪些、催生了什么新问题。
引用 memory 条目用 (memory: name@rev) 标注；引用证据落到具体周/论文。

## 下月关注
2-4 个 bullet：基于本月脉络，下月值得盯的方向/会议/仓库。

## 蒸馏
月度级别的 memory 候选（比周报更收敛，只留跨周成立的）；每条一行：
`- DRAFT[<target_path>]: <内容>`
target_path 规则同周报（topics/ 等前缀 + .md）。没有就写 `(本月无蒸馏建议)`。

不要输出五段以外的任何内容。"""


def iso_weeks_of_month(year: int, month: int) -> list[str]:
    """ISO week labels whose Thursday falls inside (year, month)."""
    labels: list[str] = []
    d = date(year, month, 1)
    while d.month == month:
        if d.weekday() == 3:  # Thursday anchors the ISO week
            y, w, _ = d.isocalendar()
            labels.append(f"{y}-W{w:02d}")
        d += timedelta(days=1)
    return labels


def gather_weekly_bodies(topic_id: str, week_labels: list[str]) -> list[dict]:
    """Read the month's weekly artifacts from the local mirror; figures are
    stripped (the monthly is text synthesis) and each week is char-capped."""
    mirror = Path(os.getenv("ISBE_ARTIFACT_MIRROR", "artifacts"))
    out: list[dict] = []
    for label in week_labels:
        path = mirror / topic_id / label / "latest.md"
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        body = _DATAURI_RE.sub("[图]", body)[:_PER_WEEK_CHAR_CAP]
        out.append({"label": label, "body": body})
    return out


def _split_sections(text: str) -> dict[str, str]:
    """Generic level-2 section splitter (monthly headers differ from weekly)."""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in (text or "").splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = m.group(1)
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


@flow(name="monthly-digester")
def monthly_digester(
    topic_id: str,
    month_label: str | None = None,
    today: date | None = None,
) -> DigestResult:
    """Monthly rollup for one topic. `month_label` like "2026-08"; defaults to
    the PREVIOUS month (the natural cron target on the 1st of a month)."""
    today = today or date.today()
    if month_label is None:
        first = today.replace(day=1) - timedelta(days=1)
        month_label = f"{first.year}-{first.month:02d}"
    year, month = (int(x) for x in month_label.split("-"))

    cfg = load_topic_config(default_topics_root(), topic_id)
    topic_label = cfg.get("label", topic_id)

    with topic_run(topic_id, "monthly-digester") as run:
        weeks = iso_weeks_of_month(year, month)
        weeklies = gather_weekly_bodies(topic_id, weeks)
        run.payload["month"] = month_label
        run.payload["weeks_found"] = [w["label"] for w in weeklies]
        if not weeklies:
            run.payload["skipped"] = "no weekly artifacts for this month"
            return DigestResult(
                topic_id=topic_id,
                period_label=month_label,
                generated_at=datetime.now(UTC),
                sections=[],
                fingerprint={"weeks": []},
                pending_drafts=[],
            )

        mroot = _memory_root()
        memory_block, memory_index = build_memory_block(mroot, topic_id=topic_id)
        weeks_blob = "\n\n".join(
            f"=== 周报 {w['label']} ===\n{w['body']}" for w in weeklies
        )
        user_prompt = (
            f"主题：{topic_label}\n月份：{month_label}\n\n"
            f"{weeks_blob}\n\n=== Memory (当前) ===\n{memory_block}\n\n"
            "请按 system 指令输出五段。"
        )
        resp = complete(system=MONTHLY_SYSTEM_PROMPT, user=user_prompt, max_tokens=4096)
        parts = _split_sections(resp.text)

        drafts = parse_distillation_section(parts.get("蒸馏", ""))
        for d in drafts:
            write_pending(mroot, d)

        artifact_id = uuid4()
        rendered = Template(MONTHLY_TEMPLATE.read_text(encoding="utf-8")).render(
            topic_id=topic_id,
            topic_label=topic_label,
            month_label=month_label,
            overview=parts.get("月度总览", ""),
            must_read=parts.get("本月必读", ""),
            thesis_evolution=parts.get("论点演化", ""),
            watch_next=parts.get("下月关注", ""),
            distillation=parts.get("蒸馏", ""),
            weeks=[w["label"] for w in weeklies],
            memory_refs=", ".join(f"{k}@rev{v}" for k, v in memory_index.items()),
            trace_id=resp.trace_id or "(none)",
            generated_at=datetime.now(UTC).isoformat(),
            artifact_id=str(artifact_id),
        )
        save_artifact(
            topic_id=topic_id,
            kind="monthly_digest",
            period_label=month_label,
            body_markdown=rendered,
            fingerprint={"weeks": [w["label"] for w in weeklies], "memory": memory_index},
            generated_at=datetime.now(UTC),
            artifact_id=artifact_id,
        )

        run.payload["n_drafts"] = len(drafts)
        run.payload["artifact_id"] = str(artifact_id)
        run.payload["llm_input_tokens"] = resp.input_tokens
        run.payload["llm_output_tokens"] = resp.output_tokens

        mirror_root = Path(os.getenv("ISBE_ARTIFACT_MIRROR", "artifacts"))
        latest = mirror_root / topic_id / month_label / "latest.md"
        pushed = send_digest_notification(
            topic_label=f"{topic_label}（月报）",
            period_label=month_label,
            artifact_path=latest if latest.exists() else None,
            excerpt=(resp.text or "")[:800],
        )
        run.payload["notify_sent"] = pushed

        return DigestResult(
            topic_id=topic_id,
            period_label=month_label,
            generated_at=datetime.now(UTC),
            sections=[
                DigestSection(kind="tldr", body=parts.get("月度总览", "")),
                DigestSection(kind="analysis", body=parts.get("论点演化", "")),
                DigestSection(kind="distillation", body=parts.get("蒸馏", "")),
            ],
            fingerprint={"weeks": [w["label"] for w in weeklies], "artifact_id": str(artifact_id)},
            pending_drafts=drafts,
        )
