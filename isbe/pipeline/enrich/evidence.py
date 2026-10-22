"""What a card's quote and value must match in the paper's full text.

Looking for every number and URL of a value anywhere in the whole full text,
squeezed of whitespace, would let `## Page 3` vouch for any "3", `10.53` for
"0.53", `-0.3` for "+0.3", `1 2` for "12", and `github.com/foobar` for
`github.com/foo`; skipping quote fragments shorter than 8 characters would
show them as verbatim anyway. So:

- A quote is split into passages (lines, `;`, `…`); neighbours are joined back
  where the joined text is also in the paper, which keeps line-wrapped quotes
  whole. Every passage must be in the paper after Unicode (NFKC, case-fold,
  typographic quotes and dashes) and whitespace normalization, must not start
  or end inside a number, and must be at least 8 characters: a shorter one
  rejects the quote instead of being skipped.
- Every number of the value must be a number of the field's own (checked)
  quote: same digits and decimals, the same sign when the value has one.
  Digits glued to a Latin name (`day1`, `A100`, `ERA5`, `GPT-4`, `R@1`) name
  something and are not quantities.
- Every URL of the value must be in that quote and appear in the paper as a
  whole URL, not as the start of a longer one; its scheme and `www.` may differ
  and a line break may split it.

Whitespace between two digits survives normalization as one space, so digits
the paper keeps apart never merge; PDF glyph spacing around a decimal point
(`1 . 477`) still reads as one number.
"""

import re
import unicodedata

from .text import WHITESPACE

MIN_PASSAGE_CHARS = 8
# Shorter quotes ("Table 1", "ERA5") prove nothing.
MIN_QUOTE_CHARS = 15

_VARIANTS = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-",
    "−": "-",
    "­": None, "​": None, "‌": None, "‍": None, "⁠": None,
    "﻿": None,
})
_SPACES = re.compile(f"[{WHITESPACE}]+")
_PASSAGE = re.compile(r"(\n|;|\.\.\.)")
_TEXT = re.compile(r"[^\W_]")
_NUMBER = re.compile(r"[0-9]+(?:,[0-9]{3}(?![0-9]))*(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?")
_GLYPH_GAP = re.compile(f"(?<=[0-9])[{WHITESPACE}]+([.,])[{WHITESPACE}]+(?=[0-9])")
_URL_SPLIT = re.compile(f"[{WHITESPACE}（）()，。；;、<>\"“”'「」【】]")
_URL_CHARS = r"a-z0-9_\-~%/?#&=+@$*"
_DIGITS = frozenset("0123456789")


def fold(text: str) -> str:
    """Unicode-normalized for comparison: full-width digits and ligatures to ASCII,
    case-folded, typographic quotes and dashes to ASCII, invisible marks dropped."""
    once = unicodedata.normalize("NFKC", text)
    return unicodedata.normalize("NFKC", once.casefold()).translate(_VARIANTS)


def squeeze(folded: str) -> str:
    """Without whitespace (PDF text breaks lines and spaces glyphs where a model's
    copy does not), except one space where it separated two digits."""

    def gap(match: re.Match[str]) -> str:
        start, end = match.start(), match.end()
        between = start > 0 and end < len(folded)
        return " " if between and folded[start - 1] in _DIGITS and folded[end] in _DIGITS else ""

    return _SPACES.sub(gap, folded)


def _numeric_start(piece: str) -> bool:
    return piece[0] in _DIGITS or (piece[0] in ".," and len(piece) > 1 and piece[1] in _DIGITS)


def found(piece: str, hay: str) -> bool:
    """Whether squeezed `piece` occurs in squeezed `hay` without cutting a number."""
    start = hay.find(piece) if piece else -1
    while start >= 0:
        end = start + len(piece)
        cut = False
        if _numeric_start(piece) and start:
            before = hay[start - 1]
            cut = before in _DIGITS or (before in ".," and start > 1 and hay[start - 2] in _DIGITS)
        if not cut and piece[-1] in _DIGITS and end < len(hay):
            after = hay[end]
            cut = after in _DIGITS or (
                after in ".," and end + 1 < len(hay) and hay[end + 1] in _DIGITS
            )
        if not cut:
            return True
        start = hay.find(piece, start + 1)
    return False


def passages(quote: str, hay: str) -> list[str]:
    """The quote's passages, squeezed, each joined with its successor while the
    joined text is still in `hay`; pieces without letters or digits are skipped."""
    parts = _PASSAGE.split(fold(quote))
    groups: list[str] = []
    current: str | None = None
    gap = ""
    for i in range(0, len(parts), 2):
        piece, sep = parts[i], parts[i - 1] if i else ""
        if not _TEXT.search(piece):
            gap += sep + piece
            continue
        if current is not None:
            joined = current + gap + sep + piece
            if found(squeeze(joined), hay):
                current, gap = joined, ""
                continue
            groups.append(squeeze(current))
        current, gap = piece, ""
    if current is not None:
        groups.append(squeeze(current))
    return groups


def quoted(quote: str, hay: str) -> bool:
    """Every passage is in the text and long enough to mean something; together
    they say at least `MIN_QUOTE_CHARS` characters."""
    groups = passages(quote, hay)
    return (
        bool(groups)
        and all(len(g) >= MIN_PASSAGE_CHARS and found(g, hay) for g in groups)
        and sum(len(g) for g in groups) >= MIN_QUOTE_CHARS
    )


def numbers(folded: str) -> list[tuple[str, str]]:
    """(sign, digits) of each quantity; digits glued to a Latin name are skipped."""
    text = _GLYPH_GAP.sub(r"\1", folded)
    out = []
    for match in _NUMBER.finditer(text):
        start = match.start()
        before = text[start - 1] if start else ""
        prior = text[start - 2] if start > 1 else ""
        if _name(before) or (before in ("-", "_", "@") and _name(prior)):
            continue
        sign = before if before in ("+", "-") and not (prior.isascii() and prior.isalnum()) else ""
        out.append((sign, match.group()))
    return out


def _name(c: str) -> bool:
    return c == "_" or ("a" <= c <= "z")


def urls(value: str) -> list[str]:
    """The URLs a value states."""
    found_urls = []
    for token in _URL_SPLIT.split(value):
        if "://" in token or token.startswith("github.com") or token.startswith("www."):
            found_urls.append(token.rstrip(".,:："))
    return found_urls


def stated(value: str) -> list[str]:
    """The numbers (with their sign) and URLs a value states, which its quote must hold."""
    links = urls(value)
    rest = value
    for link in links:
        rest = rest.replace(link, " ")
    return [sign + digits for sign, digits in numbers(fold(rest))] + links


def _url_core(url: str) -> str:
    core = squeeze(fold(url))
    core = core.split("://", 1)[1] if "://" in core else core
    return core.removeprefix("www.").rstrip("/")


def url_in(folded: str, core: str) -> bool:
    """Whether `core` (no scheme, no `www.`) is a whole URL of `folded`, tolerating
    line breaks inside it."""
    body = f"[{WHITESPACE}]*".join(re.escape(c) for c in core)
    pattern = (
        rf"(?:(?<=://)|(?<![a-z0-9_\-.~%@/:]))(?:www\.)?{body}/?"
        rf"(?![{_URL_CHARS}]|[.,:;!]+[{_URL_CHARS}])"
    )
    return re.search(pattern, folded) is not None


def supported(value: str, quote: str, source: str) -> bool:
    """Every number and URL of `value` is in `quote`; URLs also whole in `source`
    (the folded full text). `quote` must already have passed `quoted`."""
    links = urls(value)
    rest = value
    for link in links:
        rest = rest.replace(link, " ")
    # Grouping commas aside ("2,288" is "2288"), the digits must match exactly; a
    # signed value needs the same sign, an unsigned one takes either.
    have = {(sign, digits.replace(",", "")) for sign, digits in numbers(fold(quote))}
    for sign, digits in numbers(fold(rest)):
        plain = digits.replace(",", "")
        if not any(d == plain and (not sign or s == sign) for s, d in have):
            return False
    squeezed = squeeze(fold(quote))
    for link in links:
        core = _url_core(link)
        if not core or core not in squeezed or not url_in(source, core):
            return False
    return True
