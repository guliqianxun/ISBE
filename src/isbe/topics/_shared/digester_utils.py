"""Pure helpers shared between digester flavors (arxiv-weekly + finance-daily).

No flow decorators, no DB access, no LLM calls — just text processing
and memory loading utilities.
"""
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from isbe.memory.loader import load_index
from isbe.topics.base import PendingMemoryDraft
from isbe.triage import Item, RetrievalContract, TriageResult, triage


@dataclass(frozen=True)
class DigestRow:
    """本地源 S2Paper → digester 鸭子类型行。

    暴露 _build_facts_block / paper_to_item / comparison 需要的 6 个属性，
    使本地源论文无需 Paper ORM 即可走完现有 digester 路径。
    """

    arxiv_id: str
    title: str
    abstract: str | None
    primary_category: str
    source_url: str
    submitted_at: datetime | None


def s2paper_to_digest_row(p) -> DigestRow:
    """本地源 S2Paper → DigestRow（published_at 字符串 → datetime）。"""
    pub: datetime | None = None
    if p.published_at:
        try:
            pub = datetime.fromisoformat(p.published_at)
        except ValueError:
            pub = None
    return DigestRow(
        arxiv_id=p.arxiv_id or p.id,
        title=p.title,
        abstract=p.abstract,
        primary_category=(p.fields_of_study[0] if p.fields_of_study else ""),
        source_url=p.url,
        submitted_at=pub,
    )


def paper_to_item(p) -> Item:
    """Paper ORM 行 → triage Item（duck-typed，不 import ORM，守模块边界）。"""
    return Item(
        id=p.arxiv_id, source="arxiv", headline=p.title, summary=p.abstract,
        url=p.source_url, published_at=p.submitted_at,
    )


def article_to_item(a) -> Item:
    """Article ORM 行 → triage Item。"""
    return Item(
        id=a.id, source=a.source, headline=a.headline, summary=a.summary,
        url=a.url, published_at=a.published_at,
    )


def apply_triage(
    rows: list,
    contract: RetrievalContract | None,
    to_item: Callable[[object], Item],
) -> tuple[list, TriageResult | None]:
    """对 facts 行跑 triage，返回 (kept_rows, result)。

    迁移安全网：contract 为 None（未写 `retrieval:` 块）→ 原样返回，行为不变。
    保持 rows 与 items 顺序对应，按 kept_ids 过滤回原始行（下游照常用 ORM 对象）。
    """
    if contract is None:
        return list(rows), None
    items = [to_item(r) for r in rows]
    result = triage(items, contract)
    kept_ids = result.kept_ids
    kept_rows = [r for r, it in zip(rows, items, strict=True) if it.id in kept_ids]
    return kept_rows, result


def facts_window(today: date, *, lookback_days: int) -> tuple[datetime, datetime]:
    """Compute the [low, high] datetime range used to filter facts in a digest.

    Returned pair is intended for `col >= low AND col <= high`. The upper
    bound is end-of-day on `today` (23:59:59.999999 UTC) so same-day items
    are included; without it, items submitted after `today` but before the
    digest actually ran would leak into the report.
    """
    low = datetime.combine(today - timedelta(days=lookback_days), time.min, tzinfo=UTC)
    high = datetime.combine(today, time.max, tzinfo=UTC)
    return low, high

DRAFT_LINE_RE = re.compile(r"^\s*-\s*DRAFT\[([^\]]+)\]:\s*(.+)$")

VALID_TYPE_PREFIXES = {
    "topics": "topic",
    "reading": "reading",
    "feedback": "feedback",
    "user": "user",
    "reference": "reference",
}


def memory_root() -> Path:
    raw = os.getenv("ISBE_MEMORY_ROOT")
    if raw:
        return Path(raw)
    uid = os.getenv("ISBE_UID", "me")
    return Path("memory") / uid


def build_memory_block(
    memory_root_path: Path,
    *,
    topic_id: str | None = None,
) -> tuple[str, dict]:
    """Returns (text_block, {name: revision} index) for relevant memory entries.

    When `topic_id` is given, type=topic entries are filtered to those whose
    `name` equals the topic_id or starts with `<topic_id>.` (sub-document
    convention, e.g. `nowcasting.theses`). Other topics' notes never enter
    the prompt — that's the bug this fixes: cross-topic pollution where a
    video-generation digest sees nowcasting.theses and writes drafts to it.

    type=feedback and type=user are global preferences and always included,
    regardless of topic_id.

    When `topic_id` is None, behavior is the legacy "load all" — kept so
    older test fixtures and any non-digester caller don't break silently.
    """
    index: dict[str, int] = {}
    chunks: list[str] = []
    for entry in load_index(memory_root_path):
        ftype = entry.frontmatter.type
        if ftype.value not in ("topic", "feedback", "user"):
            continue
        if ftype.value == "topic" and topic_id is not None:
            name = entry.frontmatter.name
            if name != topic_id and not name.startswith(f"{topic_id}."):
                continue
        index[entry.frontmatter.name] = entry.frontmatter.revision
        chunks.append(
            f"--- {entry.frontmatter.name}@rev{entry.frontmatter.revision} "
            f"(type={ftype.value}) ---\n{entry.body.strip()}"
        )
    return "\n\n".join(chunks), index


PAPER_REVIEW_RE = re.compile(r"^\s*-\s*\[([0-9.]+(?:v\d+)?)\]\s*:?\s*(.+)$")
REPO_REVIEW_RE = re.compile(r"^\s*-\s*\[([^\]]+)\]\s*:?\s*(.+)$")


def split_sections(text: str) -> dict[str, str]:
    """Split LLM output by markdown level-2 headers into the six known section keys."""
    sections: dict[str, str] = {}
    current_key = None
    buf: list[str] = []
    name_map = {
        "TL;DR": "tldr",
        # arxiv-weekly
        "论文逐篇": "paper_reviews",
        "仓库逐条": "repo_reviews",
        # nvda-daily
        "新闻逐条": "news_reviews",
        "SEC 逐条": "filing_reviews",
        # motorcycle-weekly / china-tech-weekly
        "文章逐条": "article_reviews",
        "品牌动态": "brand_notes",
        # china-tech-weekly
        "公司动态": "company_notes",
        # research-weekly beginner lens
        "名词": "glossary",
        "分析": "analysis",
        "蒸馏": "distillation",
    }
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if current_key is not None:
                sections[current_key] = "\n".join(buf).strip()
            header = stripped[3:].strip()
            current_key = name_map.get(header)
            buf = []
        else:
            if current_key is not None:
                buf.append(line)
    if current_key is not None:
        sections[current_key] = "\n".join(buf).strip()
    return sections


def parse_paper_reviews(text: str) -> dict[str, str]:
    """Extract `{arxiv_id: review_text}` from a `## 论文逐篇` block.

    Tolerates either `- [2605.10046] body` or `- [2605.10046]: body`.
    Strips trailing `vN` version suffix to match the bare `arxiv_id` used in DB.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = PAPER_REVIEW_RE.match(line)
        if not m:
            continue
        aid = m.group(1).split("v")[0]
        out[aid] = m.group(2).strip()
    return out


@dataclass(frozen=True)
class SotaClaim:
    """One SOTA-increment claim.

    Canonical (table-renderable) shape::

        <指标>@<数据集>[ vs <基线模型>]: <基线>→<新值>

    `delta_pct` is computed (not from the LLM) when baseline/new are numeric, so
    a specialist can read the increment without expanding the abstract. A claim
    that isn't canonical but still carries a number + arrow is kept in `raw` as a
    note; junk / placeholders are dropped upstream (`_parse_sota`)."""

    metric: str
    dataset: str
    baseline: str
    new: str
    raw: str
    baseline_model: str = ""
    delta_pct: str = ""

    @property
    def structured(self) -> bool:
        return bool(self.metric and self.new)


@dataclass(frozen=True)
class PaperBlock:
    """Structured per-paper payload for the research weekly card.

    Verifiability-first (what a researcher can check / reproduce, not a bare
    rank): `provenance` (作者/机构 + 本文自述的前作血统), `method` (方法 + 背景/
    所基于的工作), `data` (数据集来源 + 公开/自采), `repro` (复现风险). Judgment:
    `verdict` (≤2 句). Beginner: `plain` (大白话). `sota` is a *secondary*
    reference signal (effect numbers), not the headline.

    All fields degrade gracefully to empty — a paper the LLM under-fills still
    renders its title + abstract, so the report never loses an item to a parse
    miss.
    """

    arxiv_id: str
    verdict: str = ""
    plain: str = ""
    provenance: str = ""
    method: str = ""
    data: str = ""
    code: str = ""
    sota: tuple[SotaClaim, ...] = ()
    repro: dict[str, str] = field(default_factory=dict)


_BLOCK_HEADER_RE = re.compile(r"^\s*#{2,4}\s*\[?([0-9]+\.[0-9]+(?:v\d+)?)\]?")
_FIELD_RE = re.compile(
    r"^\s*-?\s*(评价|速览|来源|方法|数据|代码|复现|SOTA|效果)\s*[:：]\s*(.+)$", re.IGNORECASE
)
# LLM-facing label → internal field key (collapses synonyms / latin case).
_FIELD_KEY = {"sota": "SOTA", "效果": "SOTA"}
_ARROW_RE = re.compile(r"→|->")
_VS_RE = re.compile(r"\bvs\.?\b|对比", re.IGNORECASE)
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_REPRO_KEYS = ("开源", "权重", "算力", "代码完整度")


def _delta_pct(baseline: str, new: str) -> str:
    """Relative increment as a signed percent string, or '' if non-numeric.

    Pulls the first number out of each cell so `0.41` / `41.2M` / `12.3 FID`
    all reduce to a float; division-by-zero and parse failure both yield ''."""
    bm, nm = _NUM_RE.search(baseline), _NUM_RE.search(new)
    if not bm or not nm:
        return ""
    try:
        b, n = float(bm.group()), float(nm.group())
    except ValueError:
        return ""
    if b == 0:
        return ""
    return f"{(n - b) / abs(b) * 100:+.1f}%"


def _parse_one_sota(part: str) -> SotaClaim | None:
    """Parse one `<指标>@<数据集>[ vs <基线模型>]: <基线>→<新值>` claim.

    Procedural (not one mega-regex) so each segment is robust: metric may itself
    contain `@` (e.g. `CSI@8mm/h`), so dataset is taken after the LAST `@`; the
    optional `vs <模型>` is split off the dataset segment. Returns None if the
    shape isn't canonical (caller decides whether to keep a raw note or drop)."""
    head_tail = re.split(r"[:：]", part, maxsplit=1)
    if len(head_tail) != 2:
        return None
    head, tail = head_tail
    if "@" not in head:
        return None
    metric, _, rest = head.rpartition("@")
    metric = metric.strip()
    vs_split = _VS_RE.split(rest, maxsplit=1)
    dataset = vs_split[0].strip()
    model = vs_split[1].strip() if len(vs_split) > 1 else ""
    arrow = _ARROW_RE.split(tail, maxsplit=1)
    if len(arrow) != 2:
        return None
    baseline, new = arrow[0].strip(), arrow[1].strip()
    if not metric or not new:
        return None
    return SotaClaim(
        metric=metric, dataset=dataset, baseline=baseline, new=new, raw=part.strip(),
        baseline_model=model, delta_pct=_delta_pct(baseline, new),
    )


def _parse_sota(raw: str) -> tuple[SotaClaim, ...]:
    """Parse a `- SOTA:` field into claims, **structured-or-drop**.

    Each `;`-separated part is parsed canonically; a non-canonical part is kept
    as a raw note ONLY if it contains both a number and an increment arrow (a
    real-but-loosely-formatted metric). Everything else — placeholders like
    `(无明确 SOTA 声明)`, `暂无`, prose without numbers — is dropped, so the
    template never renders a junk bullet under the "SOTA 增量" heading."""
    raw = raw.strip()
    if not raw:
        return ()
    claims: list[SotaClaim] = []
    for part in re.split(r"[;；]", raw):
        part = part.strip().strip("|").strip()  # tolerate stray markdown-table pipes
        if not part:
            continue
        claim = _parse_one_sota(part)
        if claim is not None:
            claims.append(claim)
        elif _NUM_RE.search(part) and _ARROW_RE.search(part):
            claims.append(
                SotaClaim(metric="", dataset="", baseline="", new="", raw=part)
            )
        # else: placeholder / prose without a metric → dropped
    return tuple(claims)


_REPRO_SEP_RE = re.compile(r"[=:：]")


def _parse_repro(raw: str) -> dict[str, str]:
    """Parse `开源=是 · 权重=否 · 算力=1×A100 · 代码完整度=中` into a keyed dict.

    Tolerates `·,，、` separators between pairs and `=`/`:`/`：` between key and
    value (LLM drift). Unknown keys are ignored."""
    out: dict[str, str] = {}
    for part in re.split(r"[·,，、]", raw):
        kv = _REPRO_SEP_RE.split(part.strip(), maxsplit=1)
        if len(kv) != 2:
            continue
        k, v = kv[0].strip(), kv[1].strip()
        if k in _REPRO_KEYS and v:
            out[k] = v
    return out


def parse_paper_blocks(text: str) -> dict[str, "PaperBlock"]:
    """Parse the structured `## 论文逐篇` section into `{arxiv_id: PaperBlock}`.

    Block format (per paper)::

        ### [<arxiv_id>]
        - 评价: <≤2 句价值判断>
        - 速览: <大白话 1 句>
        - 来源: <作者/机构 + 本文自述的前作血统>
        - 方法: <核心方法 + 所基于的工作（背景）>
        - 数据: <数据集 + 公开/自采 + 来源>
        - 复现: 开源=是 · 权重=否 · 算力=1×A100 · 代码完整度=中
        - 效果: <指标>@<数据集> vs <基线模型>: <基线>→<新值>  | (无明确数字)

    Tolerant by design: full/half-width colons, optional leading `-`, missing
    fields, and the legacy one-line `- [<id>] <verdict>` shape (mapped to
    `verdict` only) all parse without raising. Unknown lines are ignored.
    """
    blocks: dict[str, PaperBlock] = {}
    cur_id: str | None = None
    fields: dict[str, str] = {}

    def _flush() -> None:
        if cur_id is None:
            return
        blocks[cur_id] = PaperBlock(
            arxiv_id=cur_id,
            verdict=fields.get("评价", ""),
            plain=fields.get("速览", ""),
            provenance=fields.get("来源", ""),
            method=fields.get("方法", ""),
            data=fields.get("数据", ""),
            code=fields.get("代码", ""),
            sota=_parse_sota(fields.get("SOTA", "")),
            repro=_parse_repro(fields.get("复现", "")),
        )

    for line in text.splitlines():
        hm = _BLOCK_HEADER_RE.match(line)
        if hm:
            _flush()
            cur_id = hm.group(1).split("v")[0]
            fields = {}
            continue
        # legacy one-line form: `- [<id>] <verdict>`
        lm = PAPER_REVIEW_RE.match(line)
        if lm and cur_id is None:
            aid = lm.group(1).split("v")[0]
            blocks[aid] = PaperBlock(arxiv_id=aid, verdict=lm.group(2).strip())
            continue
        fm = _FIELD_RE.match(line)
        if fm and cur_id is not None:
            label = fm.group(1)
            key = _FIELD_KEY.get(label.lower(), label)
            fields[key] = fm.group(2).strip()
    _flush()
    return blocks


def parse_glossary(text: str) -> list[tuple[str, str]]:
    """Parse a `## 名词` glossary block into ordered `(term, gloss)` pairs.

    Line format: `- <术语>: <一句解释>`. Beginner-lens content; empty/placeholder
    sections yield `[]` so the template can omit the section entirely.
    """
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        m = re.match(r"^\s*-\s*(.+?)\s*[:：]\s*(.+)$", line)
        if m:
            term, gloss = m.group(1).strip(), m.group(2).strip()
            if term and gloss:
                out.append((term, gloss))
    return out


def parse_bracketed_reviews(text: str) -> dict[str, str]:
    """Extract `{key: review_text}` from any `- [<key>] <text>` block.

    Used for `## 仓库逐条` (key = repo title), `## 新闻逐条` (key = news.id sha1),
    `## SEC 逐条` (key = accession_no), `## 文章逐条` (key = article.id[:12]).

    Normalizes a leading `id=` prefix so both `[id=abc123]:` and `[abc123]:`
    map to the same key — the LLM oscillates between the two and getting
    bitten by the former is the root cause of "评价 all —" in article reports.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = REPO_REVIEW_RE.match(line)
        if not m:
            continue
        key = m.group(1).strip()
        if key.startswith("id="):
            key = key[3:]
        out[key] = m.group(2).strip()
    return out


def parse_distillation_section(text: str) -> list[PendingMemoryDraft]:
    """Parse `- DRAFT[<target_path>]: <body>` lines into PendingMemoryDraft objects."""
    import sys

    drafts: list[PendingMemoryDraft] = []
    for line in text.splitlines():
        m = DRAFT_LINE_RE.match(line)
        if not m:
            continue
        target_path = m.group(1).strip()
        content = m.group(2).strip()

        prefix = target_path.split("/", 1)[0]
        if prefix not in VALID_TYPE_PREFIXES:
            print(f"[digester] skip DRAFT (bad prefix '{prefix}'): {target_path}", file=sys.stderr)
            continue
        if not target_path.endswith(".md"):
            print(f"[digester] skip DRAFT (not .md): {target_path}", file=sys.stderr)
            continue
        target_type = VALID_TYPE_PREFIXES[prefix]

        body = (
            f"---\nname: {Path(target_path).stem}\n"
            f"description: agent draft from digest\n"
            f"type: {target_type}\n"
            f"created: {date.today().isoformat()}\n"
            f"updated: {date.today().isoformat()}\n"
            f"source: agent-inferred\nrevision: 1\n---\n{content}\n"
        )
        drafts.append(
            PendingMemoryDraft(
                target_type=target_type,
                target_path=target_path,
                body=body,
                rationale="extracted from digest distillation section",
            )
        )
    return drafts
