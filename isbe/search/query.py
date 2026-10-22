"""Search-query syntax, as in Google Scholar .

Words separated by spaces must all appear; an uppercase `OR` between two words or
phrases means either (lowercase "or" is an ordinary word); `"..."` is an exact
phrase; `-word` or `-"phrase"` excludes; `site:domain` and `intitle:word` (or
`intitle:"phrase"`) are operators. Chinese quotes “...” count as quotes.

The text sent is the line as the user wrote it, normalized (spaces collapsed,
quotes made ASCII), plus the plan's region and institution as quoted phrases.
Nothing is expanded, translated or reordered. Whether the engines honour the
syntax is not checked (see `capabilities`), so every operator a query uses is
listed for checking in the results, together with region and institution, which
no search request can filter on.
"""

import re
from dataclasses import dataclass, field

from isbe.core.errors import HttpError

_QUOTES = str.maketrans({"“": '"', "”": '"'})
_OPERATOR = re.compile(r"(site|intitle|author|source):", re.IGNORECASE)
# Scholar fields a web search has nothing to match against.
_SCHOLAR_ONLY = {"author", "source"}
_OR_MISUSED = "OR must join two words or phrases (A OR B)"


@dataclass
class Query:
    """One parsed query line; `any` holds the OR groups as written (phrases quoted)."""

    terms: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)
    any: list[list[str]] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    sites: list[str] = field(default_factory=list)
    intitle: list[str] = field(default_factory=list)
    text: str = ""


def _refuse(line: str, problem: str) -> HttpError:
    return HttpError(400, f"Query “{line}”: {problem}")


def _tokens(line: str) -> list[str]:
    """Whitespace-separated tokens. A quoted phrase stays one token, together with
    what is glued to its opening quote (`-`, `intitle:`); its inner spaces collapse."""
    text = line.translate(_QUOTES)
    if text.count('"') % 2:
        raise _refuse(line, 'close every quotation mark (")')
    tokens: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        start = i
        while i < n and not text[i].isspace() and text[i] != '"':
            i += 1
        if i < n and text[i] == '"':
            close = text.index('"', i + 1)
            inner = " ".join(text[i + 1 : close].split())
            if not inner:
                raise _refuse(line, "a quoted phrase is empty")
            tokens.append(f'{text[start:i]}"{inner}"')
            i = close + 1
        else:
            tokens.append(text[start:i])
    return tokens


def parse(line: str) -> Query:
    """The query's parts, or `HttpError(400)` naming what to fix."""
    query = Query()
    tokens = _tokens(line)
    # Per token, the word or phrase an OR may join; None for operators and exclusions.
    joinable: list[str | None] = []
    for token in tokens:
        operator = _OPERATOR.match(token)
        if token == "OR":
            joinable.append(None)
        elif operator:
            name, value = operator.group(1).lower(), token[operator.end() :]
            if name in _SCHOLAR_ONLY:
                raise _refuse(
                    line,
                    f"{name}: is a Google Scholar field that web search cannot apply; "
                    'put the name in quotes instead, e.g. "Name"',
                )
            if not value:
                raise _refuse(line, f"{name}: needs a value")
            if name == "site" and '"' in value:
                raise _refuse(line, "site: takes a domain, not a phrase")
            (query.sites if name == "site" else query.intitle).append(value.strip('"'))
            joinable.append(None)
        elif token.startswith("-"):
            if token == "-":
                raise _refuse(line, "a minus sign must be followed by the word or phrase to exclude")
            query.excluded.append(token[1:])
            joinable.append(None)
        else:
            joinable.append(token)
    i = 0
    while i < len(tokens):
        term = joinable[i]
        if tokens[i] == "OR":
            raise _refuse(line, _OR_MISUSED)
        group = [term]
        while term is not None and i + 1 < len(tokens) and tokens[i + 1] == "OR":
            following = joinable[i + 2] if i + 2 < len(tokens) else None
            if following is None:
                raise _refuse(line, _OR_MISUSED)
            group.append(following)
            i += 2
        if len(group) > 1:
            query.any.append([t for t in group if t is not None])
        elif term is not None and term.startswith('"'):
            query.phrases.append(term[1:-1])
        elif term is not None:
            query.terms.append(term)
        i += 1
    if not (query.terms or query.phrases or query.any or query.intitle):
        raise _refuse(line, "add at least one word or phrase to search for")
    query.text = " ".join(tokens)
    return query


def compile(line: str, region: str = "", institution: str = "") -> str:
    """The `q` sent for `line`: its normalized text plus region and institution as
    quoted phrases, each left out when the line already contains it."""
    text = parse(line).text
    for extra in (region, institution):
        if extra and extra.casefold() not in text.casefold():
            text += f' "{extra}"'
    return text


def constraints(line: str, region: str = "", institution: str = "") -> list[str]:
    """The constraints a run of `line` asks for, named and ordered as in
    `capabilities.CONSTRAINTS` (which imports plans, which imports this module)."""
    query = parse(line)
    used = {
        "terms": True,
        "phrase": bool(query.phrases) or any(t.startswith('"') for g in query.any for t in g),
        "boolean": bool(query.any),
        "exclusion": bool(query.excluded),
        "site": bool(query.sites),
        "title": bool(query.intitle),
        "institution": bool(institution),
        "region": bool(region),
        "language": True,
        "publication_window": True,
    }
    return [name for name, on in used.items() if on]
