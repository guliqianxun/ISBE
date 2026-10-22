"""Report Markdown → sanitized HTML.

CommonMark with tables and strikethrough, then nh3, the Python binding of
ammonia, with ammonia's defaults: its tag and
attribute allow-lists, URL schemes, comment stripping and `rel="noopener
noreferrer"` on links. One deliberate restriction: an image
may only show a local report figure (`/figures/...`); remote, `data:` and any
other image is removed, so opening a report never contacts a third party.
"""

import re
from collections.abc import Callable
from functools import cache
from itertools import pairwise
from typing import Any

import nh3
from markdown_it import MarkdownIt

# A figure name as the digest and media bundles write them, below /figures/.
_FIGURE = re.compile(r"/figures/[A-Za-z0-9_-][A-Za-z0-9._-]*(?:/[A-Za-z0-9_-][A-Za-z0-9._-]*)*")
_IMG = re.compile(r"<img[\s/>]")
_SRC = re.compile(r'\ssrc="')


@cache
def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark").enable(["table", "strikethrough"])

    # markdown-it writes strikethrough as <s>; reports have always used <del>.
    def strike_open(self: Any, tokens: Any, idx: int, options: Any, env: Any) -> str:
        return "<del>"

    def strike_close(self: Any, tokens: Any, idx: int, options: Any, env: Any) -> str:
        return "</del>"

    md.add_render_rule("s_open", strike_open)
    md.add_render_rule("s_close", strike_close)
    for name, html in _TABLE.items():
        md.add_render_rule(name, _table_rule(name, html))
    return md


# The table layout reports have always had: the head on one line, one line per body row,
# and an empty <tbody> when there is no body row. Cell alignment is a `style`
# attribute, which the sanitizer drops, so cells carry none.
_TABLE = {
    "table_open": "<table>",
    "thead_open": "<thead>",
    "thead_close": "</thead>",
    "tbody_open": "<tbody>\n",
    "tbody_close": "</tbody>",
    "tr_open": "<tr>",
    "tr_close": "</tr>",
    "th_open": "<th>",
    "th_close": "</th>",
    "td_open": "<td>",
    "td_close": "</td>",
    "table_close": "</table>\n",
}


def _table_rule(name: str, html: str) -> Any:
    def rule(self: Any, tokens: Any, idx: int, options: Any, env: Any) -> str:
        if name == "table_open":
            env["table_body"] = False
        elif name == "tbody_open":
            env["table_body"] = True
        elif name == "tr_close" and env.get("table_body"):
            return html + "\n"
        elif name == "table_close" and not env.get("table_body"):
            return "<tbody>\n</tbody>" + html
        return html

    return rule


def local_figure(src: str) -> bool:
    """Whether an image source is a figure the console itself serves."""
    return _FIGURE.fullmatch(src) is not None


def _attribute(element: str, attribute: str, value: str) -> str | None:
    if element == "img" and attribute == "src" and not local_figure(value):
        return None
    return value


def _drop_sourceless_images(html: str) -> str:
    """Remove `<img>` tags left without `src` by the attribute filter.

    The input is ammonia's serialization: text `<` is escaped, comments are
    gone and attribute values are double-quoted with `"` escaped, so a `<`
    outside quotes always opens a tag and a `>` outside quotes closes it.
    """
    out: list[str] = []
    i = 0
    while (start := html.find("<", i)) >= 0:
        out.append(html[i:start])
        end, quoted = start + 1, False
        while end < len(html) and (quoted or html[end] != ">"):
            if html[end] == '"':
                quoted = not quoted
            end += 1
        tag = html[start : end + 1]
        if not (_IMG.match(tag) and not _SRC.search(tag)):
            out.append(tag)
        i = end + 1
    out.append(html[i:])
    return "".join(out)


# Heading ids, kept by the sanitizer only when a document asks for them.
_HEADING_IDS = {
    **nh3.ALLOWED_ATTRIBUTES,
    **{f"h{n}": {*nh3.ALLOWED_ATTRIBUTES.get(f"h{n}", ()), "id"} for n in range(1, 7)},
}
_SLUG_DROP = re.compile(r"[^\w\- ]")


def _slug_headings(tokens: list[Any]) -> None:
    """GitHub's heading anchors: the lowercased text without punctuation,
    spaces as hyphens, and `-1`, `-2` … on repeats."""
    seen: dict[str, int] = {}
    for token, inline in pairwise(tokens):
        if token.type != "heading_open":
            continue
        text = "".join(child.content for child in inline.children or ())
        slug = _SLUG_DROP.sub("", text.strip().lower()).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        token.attrSet("id", f"{slug}-{count}" if count else slug)


def render_markdown(md: str, link: Callable[[str], str | None] | None = None) -> str:
    """Figures are linked relative to the report file (`../../figures/`); the
    console serves them at `/figures/`.

    A document (rather than a report) passes `link`: it rewrites each link
    target, or drops it with None, and headings get GitHub's anchor ids so
    `#heading` links land."""
    parser = _parser()
    tokens = parser.parse(md.replace("](../../figures/", "](/figures/"))
    if link is None:
        attributes, keep = nh3.ALLOWED_ATTRIBUTES, _attribute
    else:
        _slug_headings(tokens)
        attributes = _HEADING_IDS

        def keep(element: str, attribute: str, value: str) -> str | None:
            if element == "a" and attribute == "href":
                return link(value)
            return _attribute(element, attribute, value)

    html = parser.renderer.render(tokens, parser.options, {})
    return _drop_sourceless_images(nh3.clean(html, attributes=attributes, attribute_filter=keep))
