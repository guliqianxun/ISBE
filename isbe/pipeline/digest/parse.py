"""Reading the digest reply. Lenient by design: a field
the model skipped or mangled is just absent, and the report still shows the
paper's own data.

The string helpers split lines only at `\\n` or `\\r\\n` and trim on Unicode
White_Space, so a reply splits into the same sections, blocks and claims
wherever it was produced.
"""

import re
from dataclasses import dataclass

# Unicode White_Space; Python's `str.isspace` also counts U+001C–U+001F.
WHITESPACE = (
    "\t\n\x0b\x0c\r \x85\xa0 "
    + "".join(chr(c) for c in range(0x2000, 0x200B))
    + "    　"
)
_SPLIT_WS = re.compile(f"[{re.escape(WHITESPACE)}]+")
_COLON = re.compile("[:：]")


def lines(text: str) -> list[str]:
    """Split at `\\n` or `\\r\\n`; no empty last line for a trailing break."""
    parts = text.split("\n")
    out = [p[:-1] if p.endswith("\r") else p for p in parts[:-1]]
    if parts[-1]:
        out.append(parts[-1])  # a final line without `\n` keeps a bare `\r`
    return out


def trim(s: str) -> str:
    return s.strip(WHITESPACE)


def split_whitespace(s: str) -> list[str]:
    return [w for w in _SPLIT_WS.split(s) if w]


def _split_colon(s: str) -> tuple[str, str] | None:
    """`split_once([':', '：'])`."""
    m = _COLON.search(s)
    return None if m is None else (s[: m.start()], s[m.end() :])


def _ascii_digit(c: str) -> bool:
    return "0" <= c <= "9"


_SECTIONS = {
    "TL;DR": "tldr",
    "论文逐篇": "papers",
    "Papers": "papers",
    "仓库逐条": "repos",
    "Repos": "repos",
    "名词": "glossary",
    "Glossary": "glossary",
    "分析": "analysis",
    "Analysis": "analysis",
    "蒸馏": "drafts",
    "Memory drafts": "drafts",
}


def sections(text: str) -> dict[str, str]:
    """`## <heading>` sections, keyed tldr | papers | repos | glossary | analysis | drafts."""
    out: dict[str, list[str]] = {}
    cur: str | None = None
    for line in lines(text):
        t = trim(line)
        if t.startswith("## "):
            cur = _SECTIONS.get(trim(t[3:]))
            continue
        if cur is not None:
            out.setdefault(cur, []).append(line + "\n")
    return {k: trim("".join(v)) for k, v in out.items()}


# One `### [id]` block: field key (verdict, rating, plain, source, method,
# data, code, repro, results) → value.
type Block = dict[str, str]

_FIELDS = {
    "评价": "verdict",
    "verdict": "verdict",
    "推荐": "rating",
    "rating": "rating",
    "速览": "plain",
    "plain": "plain",
    "来源": "source",
    "source": "source",
    "方法": "method",
    "method": "method",
    "数据": "data",
    "data": "data",
    "代码": "code",
    "code": "code",
    "复现": "repro",
    "repro": "repro",
    "效果": "results",
    "sota": "results",
    "results": "results",
}


def paper_blocks(text: str) -> dict[str, Block]:
    """`### [2610.01234v2]` blocks, keyed by the bare arXiv id."""
    out: dict[str, Block] = {}
    cur: str | None = None
    for line in lines(text):
        t = trim(line)
        if t.startswith("##"):
            head = trim(t.lstrip("#")).lstrip("[")
            head = re.split("[\\] ]", head, maxsplit=1)[0]
            cur = strip_version(head) if any(_ascii_digit(c) for c in head) else None
            continue
        if cur is None:
            continue
        pair = _split_colon(trim(t.lstrip("-")))
        if pair is None:
            continue
        key = _FIELDS.get(trim(pair[0]).lower())
        if key is None:
            continue
        value = trim(pair[1])
        if value:
            out.setdefault(cur, {})[key] = value
    return out


def strip_version(id: str) -> str:
    """`2610.01234v2` → `2610.01234`."""
    stem, sep, n = id.rpartition("v")
    if sep and n and all(_ascii_digit(c) for c in n):
        return stem
    return id


def bracketed(text: str) -> dict[str, str]:
    """`- [key] text` lines (repos), keyed without a stray `id=` prefix."""
    out: dict[str, str] = {}
    for line in lines(text):
        t = trim(line)
        if not t.startswith("-"):
            continue
        t = trim(t[1:])
        if not t.startswith("["):
            continue
        key, sep, rest = t[1:].partition("]")
        if not sep:
            continue
        rest = trim(trim(rest).lstrip(":："))
        if rest:
            key = trim(key)
            while key.startswith("id="):
                key = key[3:]
            out[key] = rest
    return out


def glossary(text: str) -> list[tuple[str, str]]:
    """`- term: gloss` lines; placeholder lines without a colon are skipped."""
    out: list[tuple[str, str]] = []
    for line in lines(text):
        t = trim(line)
        if not t.startswith("-"):
            continue
        pair = _split_colon(t[1:])
        if pair is None:
            continue
        term, gloss = trim(pair[0]), trim(pair[1])
        if term and gloss:
            out.append((term, gloss))
    return out


def drafts(text: str) -> list[tuple[str, str]]:
    """`- DRAFT[target]: content` lines."""
    out: list[tuple[str, str]] = []
    for line in lines(text):
        t = trim(line)
        if not t.startswith("-"):
            continue
        t = trim(t[1:])
        if not t.startswith("DRAFT["):
            continue
        target, sep, rest = t[len("DRAFT[") :].partition("]")
        if not sep:
            continue
        content = trim(trim(rest).lstrip(":："))
        if content:
            out.append((trim(target), content))
    return out


@dataclass(frozen=True)
class Claim:
    """One `Results` claim: `<metric>@<dataset> vs <model>: <baseline>→<new>`."""

    metric: str
    dataset: str
    model: str
    baseline: str
    new: str
    # Computed here, never taken from the model.
    delta: str | None


def results(raw: str) -> tuple[list[Claim], list[str]]:
    """Canonical claims, plus loose ones that still carry a number and an arrow
    as notes. Placeholders such as `(无明确数字)` yield neither."""
    claims: list[Claim] = []
    notes: list[str] = []
    for part in re.split("[;；]", raw):
        part = trim(trim(part).strip("|"))
        if not part:
            continue
        c = claim(part)
        if c is not None:
            claims.append(c)
        elif number(part) is not None and ("→" in part or "->" in part):
            notes.append(part)
    return claims, notes


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def claim(part: str) -> Claim | None:
    pair = _split_colon(part)
    if pair is None:
        return None
    head, tail = pair
    # The metric may itself hold an '@' (CSI@8mm/h), so the dataset follows the last one.
    metric, sep, rest = head.rpartition("@")
    if not sep:
        return None
    lower = rest.translate(_ASCII_LOWER)
    dataset, model = rest, ""
    for s in (" vs. ", " vs ", "对比"):
        i = lower.find(s)
        if i >= 0:
            dataset, model = rest[:i], rest[i + len(s) :]
            break
    baseline, sep, new = tail.partition("→")
    if not sep:
        baseline, sep, new = tail.partition("->")
        if not sep:
            return None
    metric, new = trim(metric), trim(new)
    if not metric or not new:
        return None
    b, n = number(baseline), number(new)
    delta = f"{(n - b) / abs(b) * 100.0:+.1f}%" if b is not None and n is not None and b != 0.0 else None
    return Claim(metric, trim(dataset), trim(model), trim(baseline), new, delta)


def number(s: str) -> float | None:
    """The first number in `s` (`0.41`, `-3`, `41.2M` → 41.2)."""
    start = next((i for i, c in enumerate(s) if _ascii_digit(c)), None)
    if start is None:
        return None
    if s[:start].endswith("-"):
        start -= 1
    end = start + 1
    dot = False
    i = start + 1
    while i < len(s):
        c = s[i]
        if _ascii_digit(c):
            end = i + 1
        elif c == "." and not dot and i + 1 < len(s) and _ascii_digit(s[i + 1]):
            dot = True
        else:
            break
        i += 1
    try:
        return float(s[start:end])
    except ValueError:
        return None
