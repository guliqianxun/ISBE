"""Whitespace as the stored text and its hashes define it.

Whitespace is Unicode White_Space; Python's `str.isspace` and `\\s` also accept
the information separators U+001C..U+001F, so the set is spelled out instead.
"""

import re

# Unicode White_Space, as a regex character class body.
WHITESPACE = "\t\n\x0b\x0c\r \x85\xa0  -     　"
_RUN = re.compile(f"[{WHITESPACE}]+")


def split_whitespace(text: str) -> list[str]:
    """The parts between runs of Unicode White_Space; none are empty."""
    return [part for part in _RUN.split(text) if part]


def collapse(text: str) -> str:
    """The words of `text` joined by single spaces."""
    return " ".join(split_whitespace(text))
