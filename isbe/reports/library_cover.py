"""What a report's cover shows, read from its markdown.

ISBE's reports and the earlier Python version's share the layout: the H1's
label, the coverage window, the first TL;DR bullets and the number of paper
reviews. The parsing is exact about string handling (lines end at `\\n` or `\\r\\n`,
trimming is Unicode, byte slicing fails off a character boundary, dates follow
the grammar below), because the result is shown on the library page.
"""

from collections.abc import Iterator
from functools import cache
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token

from isbe.core.validators import is_space, trim

# `## ` headings whose bullets are the cover's summary.
_SUMMARIES = frozenset({"TL;DR", "核心判断", "本周速览"})
_EVIDENCE = "/api/research/evidence?"


def lines(text: str) -> Iterator[str]:
    """Split after `\\n`, dropping it and one `\\r` before it."""
    start = 0
    while start < len(text):
        end = text.find("\n", start)
        if end < 0:
            yield text[start:]
            return
        line = text[start:end]
        yield line[:-1] if line.endswith("\r") else line
        start = end + 1


def trim_start(value: str) -> str:
    i = 0
    while i < len(value) and is_space(value[i]):
        i += 1
    return value[i:]


def trim_end(value: str) -> str:
    end = len(value)
    while end > 0 and is_space(value[end - 1]):
        end -= 1
    return value[:end]


def _number(s: str, most: int) -> tuple[int, str] | None:
    """Up to `most` leading ASCII digits as a number, and the rest; None without a digit."""
    i = 0
    while i < len(s) and i < most and "0" <= s[i] <= "9":
        i += 1
    return (int(s[:i]), s[i:]) if i else None


def naive_date(text: str) -> bool:
    """Whether `text` is a date: `%Y-%m-%d` with
    optional whitespace around the fields, a signed or up-to-4-digit year and
    1–2 digit month and day."""
    s = trim_start(text)
    if s[:1] in ("+", "-") and s:
        got = _number(s[1:], len(s))
        if got is None:
            return False
        year, s = (-got[0] if s[0] == "-" else got[0]), got[1]
    else:
        got = _number(s, 4)
        if got is None:
            return False
        year, s = got
    fields = [year]
    for _ in range(2):
        s = trim_start(s)
        if not s.startswith("-"):
            return False
        got = _number(trim_start(s[1:]), 2)
        if got is None:
            return False
        fields.append(got[0])
        s = got[1]
    if trim_start(s):
        return False
    _, month, day = fields
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    days = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return 1 <= month <= 12 and 1 <= day <= days[month - 1]


def _window(line: str) -> str | None:
    """`> 覆盖窗口：2026-10-07 → 2026-10-14（…）` → `2026-10-07 → 2026-10-14`."""
    sep = "→" if "→" in line else "—" if "—" in line else None
    if sep is None:
        return None
    before, after = line.split(sep, 1)
    a = trim_end(before).encode()
    b = trim_start(after).encode()
    if len(b) < 10:
        return None
    try:
        # Off a character boundary the slice cannot be decoded, so there is no range.
        start, end = a[max(0, len(a) - 10) :].decode(), b[:10].decode()
    except UnicodeDecodeError:
        return None
    return f"{start} → {end}" if naive_date(start) and naive_date(end) else None


@cache
def _parser() -> MarkdownIt:
    # Plain CommonMark, no extensions.
    return MarkdownIt("commonmark")


def _plain(bullet: str) -> str:
    """The bullet's text and code, without the text of evidence links."""
    out: list[str] = []
    evidence = False

    def walk(tokens: list[Token]) -> None:
        nonlocal evidence
        for token in tokens:
            if token.type == "link_open":
                evidence = str(token.attrGet("href") or "").startswith(_EVIDENCE)
            elif token.type == "link_close":
                evidence = False
            elif token.type in ("text", "code_inline", "code_block", "fence") and not evidence:
                out.append(token.content)
            if token.children:
                walk(token.children)

    walk(_parser().parse(bullet))
    return trim("".join(out))


def cover(md: str) -> dict[str, Any]:
    """`{"label", "window", "tldr", "papers"}` for one report's markdown."""
    label: str | None = None
    window: str | None = None
    tldr: list[str] = []
    papers = 0
    in_tldr = False
    for line in map(trim, lines(md)):
        if line.startswith("# "):
            if label is None:
                label = trim(line[2:])
        elif line.startswith("## "):
            in_tldr = trim(line[3:]) in _SUMMARIES
        elif line.startswith("### ["):
            papers += 1
        elif line.startswith(">") and window is None:
            window = _window(line)
        elif in_tldr and len(tldr) < 3 and line.startswith("- "):
            tldr.append(_plain(line[2:]))
    return {"label": label, "window": window, "tldr": tldr, "papers": papers}
