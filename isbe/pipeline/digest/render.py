"""The report markdown. Facts come from the database;
judgments from the reply.

Source and model text in structured fields is untrusted: HTML-escaping alone
would let a title, abstract, author, caption, repo note or model field carry
a Markdown link or a remote image. `text()` also escapes the inline Markdown
in it, so it reads exactly as written and nothing in it is a link, an image
or a code span. The only link written
from data, a repo's URL, is checked first. The free sections (TL;DR,
analysis) stay Markdown, with image syntax disarmed; the console's
`render_markdown` sanitizes them again and drops any non-local image.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from string import punctuation

from isbe.memory import Note
from isbe.topics import Lang, Topic

from . import parse
from .model import FACTS, Paper, Repo
from .parse import Block, split_whitespace
from .review import Flag, Review


@dataclass(frozen=True)
class Input:
    topic: Topic
    period: str
    window: tuple[str, str]
    sections: dict[str, str]
    blocks: dict[str, Block]
    shown: Sequence[Paper]
    listed: Sequence[Paper]
    repos: Sequence[Repo]
    funnel: dict[str, int]
    notes: Sequence[Note]
    # Drafts written to `.pending/` (target, content); refused ones are not shown.
    drafts: Sequence[tuple[str, str]]
    review: Review
    call_id: str
    report_id: str
    generated_at: str


def report(x: Input) -> str:
    zh = x.topic.lang is Lang.ZH

    def t(a: str, b: str) -> str:
        return a if zh else b

    def section(k: str) -> str:
        return x.sections.get(k, "")

    o = [f"# {text(x.topic.label)} — {x.period}\n\n"]
    if zh:
        o.append(f"> 覆盖窗口：{x.window[0]} → {x.window[1]}（论文按 arXiv 更新日期计）\n\n")
    else:
        o.append(f"> Window: {x.window[0]} → {x.window[1]} (papers by arXiv update date)\n\n")

    # TL;DR: the counts and lists are built here, so they cannot drift from the reviews below.
    o.append("## TL;DR\n\n")
    worth = [
        short_name(p)
        for p in x.shown
        if (b := x.blocks.get(p.arxiv_id)) is not None and worth_reading(b)
    ]
    n = len(x.shown) + len(x.listed)
    if zh:
        o.append(f"- 本期 {n} 篇 / 其中 {len(worth)} 篇值得读：{'、'.join(worth)}" if worth else f"- 本期 {n} 篇 / 无强相关必读")
    else:
        o.append(f"- {n} papers / {len(worth)} worth reading in full: {', '.join(worth)}" if worth else f"- {n} papers / none to read in full")
    if x.listed:
        o.append(
            f"（逐篇评审前 {len(x.shown)} 篇，其余列于文末）"
            if zh
            else f" (the first {len(x.shown)} reviewed, the rest listed at the end)"
        )
    o.append("\n")
    if x.repos:
        active = [text(r.name) for r in x.repos if r.active]
        if zh:
            o.append(f"- 仓库活跃：{'、'.join(active)} 本期窗口内有推送\n" if active else "- 本期窗口内无仓库推送\n")
        else:
            o.append(f"- Active repos: {', '.join(active)} pushed this period\n" if active else "- No tracked repo pushed this period\n")
    if section("tldr"):
        o.append(free(section("tldr")) + "\n")

    if x.shown:
        o.append(f"\n## {t('重点论文（', 'Papers (')}{len(x.shown)}{t('）', ')')}\n")
        for p in x.shown:
            o.append(paper(zh, p, x.blocks.get(p.arxiv_id)))
    if x.listed:
        o.append(
            f"\n## 其余入选（{len(x.listed)}，未展开）\n\n"
            if zh
            else f"\n## Also kept ({len(x.listed)}, not reviewed)\n\n"
        )
        for p in x.listed:
            o.append(f"- [{text(p.arxiv_id)}] {text(p.title)} — {text(p.url)}\n")

    if x.repos:
        reviews = parse.bracketed(section("repos"))
        o.append(f"\n## {t('仓库活跃度（', 'Repos (')}{len(x.repos)}{t('）', ')')}\n\n")
        o.append(t("| Repo | Stars | 最近推送 | 评价 |\n", "| Repo | Stars | Last push | Note |\n"))
        o.append("| --- | ---: | --- | --- |\n")
        for r in x.repos:
            short = r.name.rsplit("/", 1)[-1]
            note = reviews.get(r.name, reviews.get(short))
            name = f"[{cell(r.name)}]({r.url})" if link_url(r.url) else cell(r.name)
            stars = "—" if r.stars is None else str(r.stars)
            pushed = "—" if r.pushed is None else cell(r.pushed)
            o.append(f"| {name} | {stars} | {pushed} | {'—' if note is None else cell(note)} |\n")

    glossary = parse.glossary(section("glossary"))
    if glossary:
        o.append(f"\n## {t('名词（入门）', 'Glossary')}\n\n")
        o.append(t("| 术语 | 一句话解释 |\n", "| Term | Meaning |\n"))
        o.append("| --- | --- |\n")
        for term, gloss in glossary:
            o.append(f"| {cell(term)} | {cell(gloss)} |\n")

    o.append(f"\n## {t('分析', 'Analysis')}\n\n{free(section('analysis'))}\n")
    if x.drafts:
        o.append(f"\n## {t('Memory 草稿（待 review）', 'Memory drafts (pending review)')}\n\n")
        for target, content in x.drafts:
            o.append(f"- DRAFT[{text(target)}]: {text(content)}\n")

    # Audit trail: enough to find the exact prompt, reply and memory behind this report.
    def count(k: str) -> int:
        return x.funnel.get(k, 0)

    memory = ", ".join(f"{text(n.name)}@rev{n.revision}" for n in x.notes) if x.notes else "—"
    o.append(f'\n---\n\n<details markdown="1">\n<summary>{t("Audit & 追溯", "Audit")}</summary>\n\n')
    o.append(
        f"- **generated_at**: {x.generated_at}\n- **report**: {x.report_id}\n"
        f"- **llm call**: {x.call_id}\n"
    )
    o.append(f"- **memory snapshot**: {memory}\n")
    o.append(audit_review(zh, x.review))
    total = sum(x.funnel.values())
    gated, judged, kept = count("gate_dropped"), count("judge_dropped"), count("kept")
    if zh:
        o.append(f"- **筛选**: 窗口内 {total} 条 → 规则剔除 {gated} · 判定剔除 {judged} · 入选 {kept}\n")
    else:
        o.append(f"- **triage**: {total} in window → {gated} gated out · {judged} judged out · {kept} kept\n")
    if x.shown:
        o.append(f"- **papers**: {', '.join(text(p.arxiv_id) for p in x.shown)}\n")
    if x.repos:
        o.append(f"- **repos**: {', '.join(text(r.name) for r in x.repos)}\n")
    o.append("\n</details>\n")
    return "".join(o)


def paper(zh: bool, p: Paper, b: Block | None) -> str:
    def t(a: str, e: str) -> str:
        return a if zh else e

    # A verified fact from the full text wins over the reply's reading of the abstract.
    def raw(k: str) -> str | None:
        if p.card is not None and k in p.card.fields:
            return p.card.fields[k].value
        return None if b is None else b.get(k)

    def f(k: str) -> str | None:
        v = raw(k)
        return None if v is None else text(v)

    o = [f"\n### [{text(p.arxiv_id)}] {text(p.title)}{t(' · 次级', ' · secondary') if p.secondary else ''}\n\n"]
    v, r = f("verdict"), f("rating")
    if v is not None:
        o.append(f"> **{t('本期评价', 'Verdict')}**（{r}）：{v}\n" if r is not None else f"> **{t('本期评价', 'Verdict')}**：{v}\n")
    if (plain := f("plain")) is not None:
        o.append(f">\n> {t('速览（入门）：', 'In plain words: ')}{plain}\n")
    o.append("\n")
    # Reports live at reports/<topic>/<period>.md, two levels below the data dir.
    if p.figure is not None:
        files, caption = p.figure
        for file in files:
            o.append(f"![{t('配图', 'Figure')}](../../{file})\n")
        o.append(f"<sub>{text(caption)}</sub>\n\n")
    for key, label in (("source", t("来源", "Source")), ("method", t("方法/背景", "Method")), ("data", t("数据", "Data"))):
        if (value := f(key)) is not None:
            o.append(f"- **{label}**: {value}\n")
    authors = ", ".join(text(a) for a in p.authors[:10])
    if len(p.authors) > 10:
        authors += t(" 等", " et al.")
    o.append(
        f"- **{t('作者', 'Authors')}**: {authors} · {' '.join(text(c) for c in p.categories)} · "
        f"{t('更新', 'updated')} {text(p.date)}\n- **{t('原文', 'Link')}**: {text(p.url)}\n"
    )
    for key, label in (
        ("code", t("代码", "Code")),
        ("repro", t("复现", "Repro")),
        ("compute", t("算力", "Compute")),
        ("limits", t("局限", "Limitations")),
    ):
        if (value := f(key)) is not None:
            o.append(f"- **{label}**: {value}\n")
    claims, notes = parse.results(raw("results") or "")
    if claims:
        o.append(t("\n效果（参考指标，非排名结论）：\n\n", "\nResults (reference numbers, not a ranking):\n\n"))
        o.append(t(
            "| 指标 | 数据集 | 对照基线 | 基线 | 新值 | Δ |\n",
            "| Metric | Dataset | Baseline model | Baseline | New | Δ |\n",
        ))
        o.append("| --- | --- | --- | ---: | ---: | ---: |\n")
        for c in claims:
            dataset = cell(c.dataset) if c.dataset else "—"
            model = cell(c.model) if c.model else "—"
            delta = "—" if c.delta is None else c.delta
            o.append(f"| {cell(c.metric)} | {dataset} | {model} | {cell(c.baseline)} | {cell(c.new)} | {delta} |\n")
    for note in notes:
        o.append(f"- **{t('效果', 'Results')}**: {text(note)}\n")
    if p.card is not None:
        o.append(f'\n<details markdown="1"><summary>{t("原文依据", "Quotes from the paper")}</summary>\n\n')
        for k, zh_label, en_label in FACTS:
            if (fact := p.card.fields.get(k)) is not None:
                # One line: the PDF's line breaks would split the list item.
                quote = " ".join(split_whitespace(fact.quote))
                o.append(f"- **{t(zh_label, en_label)}**: “{text(quote)}”\n")
        if p.card.rejected:
            names = [t(zh_l, en_l) for k, zh_l, en_l in FACTS if k in p.card.rejected]
            o.append(f"- {t('引文未通过核验、未采用：', 'Not used, quote failed the check: ')}{t('、', ', ').join(names)}\n")
        o.append("\n</details>\n")
    if p.summary:
        o.append(
            f'\n<details markdown="1"><summary>{t("摘要原文", "Abstract")}</summary>\n\n'
            f"{text(p.summary, block=True)}\n\n</details>\n"
        )
    return "".join(o)


def audit_review(zh: bool, r: Review) -> str:
    """The reviewer's audit line, with the claims still flagged in the text the report carries."""
    n, m = len(r.first), len(r.second or [])
    if r.error is not None:
        line = ("未完成：" if zh else "incomplete: ") + text(r.error)
    elif r.dropped:
        k = len(r.dropped)
        line = (
            f"首轮 {n} 条存疑；重写稿漏掉 {k} 篇论文的评审，保留首稿"
            if zh
            else f"{n} flagged; the rewrite left out {k} of the papers, so the first draft stands"
        )
    elif r.second is None:
        line = "观点均有证据支持" if zh else "every opinion checked against the evidence"
    elif r.rewritten:
        line = f"首轮 {n} 条存疑 → 已重写 → 残留 {m} 条" if zh else f"{n} flagged → rewritten → {m} left"
    else:
        line = (
            f"首轮 {n} 条存疑；重写后反增至 {m} 条，保留首稿"
            if zh
            else f"{n} flagged; the rewrite drew {m}, so the first draft stands"
        )
    left: list[Flag] = (r.second or []) if r.rewritten else r.first
    flagged = "".join(f"  - [{text(f.verdict)}] {text(f.claim)} — {text(f.note)}\n" for f in left)
    return f"- **{'审核' if zh else 'review'}**: {line}\n{flagged}"


def worth_reading(b: Block) -> bool:
    """The reply's own "read in full" rating, in either language."""
    r = b.get("rating")
    return r is not None and ("值得细读" in r or "read in full" in r.lower())


def short_name(p: Paper) -> str:
    """A paper's name for lists: the `Name:` prefix of its title when short, else its id."""
    head = parse.trim(re.split("[:：]", p.title, maxsplit=1)[0])
    if 1 <= len(head) <= 24 and len(head) < len(p.title):
        return text(head)
    return f"[{text(p.arxiv_id)}]"


_PUNCT = frozenset(punctuation)  # what a CommonMark backslash escapes
_HTML = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}
# Inline Markdown: links, images and reference definitions (brackets), code
# spans, emphasis and strikethrough.
_INLINE = frozenset("[]`*_~")


def text(s: str, *, block: bool = False) -> str:
    """Untrusted text as literal Markdown.

    HTML-escaped; brackets, backticks, `*`, `_` and `~` are
    backslash-escaped, so no link, image, reference definition, code span or
    emphasis can form and the text renders as written; a backslash is doubled
    where it would escape the next character (LaTeX such as `\\alpha` stays as
    it was). Line breaks become spaces, keeping the text inside its list item
    or heading, unless `block`.
    """
    out: list[str] = []
    for i, c in enumerate(s):
        if c in _HTML:
            out.append(_HTML[c])
        elif c in _INLINE:
            out.append("\\" + c)
        elif c == "\\":
            following = s[i + 1 : i + 2]
            out.append("\\\\" if not following or following in _PUNCT else "\\")
        elif c in "\r\n" and not block:
            out.append(" ")
        else:
            out.append(c)
    return "".join(out)


def cell(s: str) -> str:
    """Escaped for a markdown table cell."""
    return text(s).replace("|", "\\|")


# What a link destination may hold without quoting: RFC 3986 characters minus
# parentheses, brackets and anything Markdown or a table cell would read.
_LINK = re.compile(r"https?://[A-Za-z0-9\-._~%!$&'*+,;=:@]+(?:[/?#][A-Za-z0-9\-._~%!$&'*+,;=:@/?#]*)?")


def link_url(url: str) -> bool:
    """Whether `url` may be written as a Markdown link target."""
    return _LINK.fullmatch(url) is not None


_IMAGE = re.compile(r"!\[")
_IMG_TAG = re.compile(r"<(?=img\b)", re.IGNORECASE)


def free(md: str) -> str:
    """A free section (model Markdown): kept as written, except that it cannot
    embed an image; the report's only images are the figures `enrich` saved."""
    return _IMG_TAG.sub("&lt;", _IMAGE.sub("!\\\\[", md))
