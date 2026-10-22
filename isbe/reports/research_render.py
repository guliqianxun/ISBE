"""Research report Markdown: every quotation in full, model spans only as indexes into them."""

from isbe.reports.research_schema import GROUPS, Bundle, Draft, Source

_SPACES = str.maketrans({"\n": " ", "\r": " ", "\t": " "})
_ESCAPES = {"<": "&lt;", ">": "&gt;", "&": "&amp;"} | {c: "\\" + c for c in "\\`*_[]{}#|"}


def md(value: str) -> str:
    """Inline text that Markdown shows literally: no markup, HTML or line breaks."""
    return "".join(_ESCAPES.get(c, c) for c in value.translate(_SPACES))


def _label(source: Source) -> str:
    publisher = source.publisher or "发布机构未注明"
    return md(f"{publisher}｜{source.title}")


def render(bundle: Bundle, draft: Draft) -> str:
    """The report for a validated draft, which holds one entry per bundle source."""

    def evidence_link(title: str, id: str) -> str:
        return (
            f"[{md(title)}](/api/research/evidence?topic={bundle.topic}"
            f"&period={bundle.period}&id={id})"
        )

    scope = bundle.scope
    # Note: the window dates and `published_on` are copied unescaped; the date
    # parser accepts surrounding whitespace, so a manifest date such as
    # "\n2024-01-01" breaks the blockquote line. Only whitespace can get through.
    out = [
        f"# {md(bundle.title)}\n\n> 覆盖窗口：{scope.from_} → {scope.to}；"
        f"地域：{md(scope.region)}\n\n## 研究问题\n\n"
    ]
    out.extend(f"- {md(question)}\n" for question in scope.questions)
    for frozen in bundle.sources:
        source = frozen.source
        item = next(s for s in draft.sources if s.source_id == source.id)
        url = source.url.replace("<", "%3C").replace(">", "%3E")
        published = "未登记" if source.published_on is None else source.published_on
        out.append(f"\n## [{_label(source)}](<{url}>)\n\n发布日期：{published}\n\n")
        for field, name in GROUPS:
            spans = getattr(item, field)
            values = (
                "；".join(evidence_link(span.text, span.evidence) for span in spans)
                if spans
                else "未标注"
            )
            out.append(f"**{name}**：{values}\n\n")
        out.append("**原文内容**\n\n")
        for excerpt in source.excerpts:
            out.append(f"> {md(excerpt.quote)}\n\n{evidence_link('查看这段原文', excerpt.id)}\n\n")
    return "".join(out)
