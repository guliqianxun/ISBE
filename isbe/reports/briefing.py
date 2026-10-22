"""Reader-facing factual briefings from editor-prepared copy and frozen originals.

Quote matching checks provenance, not whether an editorial paraphrase follows
from it. No model selection, scoring, recommendations or network calls: the
media report freezes the originals, an editor writes the copy, and `render`
only validates the copy against the frozen originals and lays it out.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import ada_url

from isbe.core import jsonfmt, validators
from isbe.reports import research_compat as compat
from isbe.reports.research_compat import STR, USIZE, Field, Seq, Struct

MAX_DRAFT = 128 * 1024


class BriefingError(ValueError):
    """An editorial draft or snapshot `render` refuses."""


@dataclass
class Evidence:
    source_id: str
    paragraph: int  # zero-based position in the frozen original's paragraphs
    quote: str


@dataclass
class DataRow:
    label: str
    value: str
    scope: str
    evidence: Evidence


@dataclass
class Item:
    headline: str
    body: list[str]
    source_ids: list[str]
    evidence: list[Evidence]
    data_rows: list[DataRow]


@dataclass
class Section:
    title: str
    items: list[Item]


@dataclass
class Schedule:
    when: str
    arrangement: str
    source_id: str
    paragraph: int
    quote: str


@dataclass
class Draft:
    title: str
    intro: str
    sections: list[Section]
    schedule: list[Schedule]
    scope_note: str | None


_EVIDENCE = Struct(
    "Evidence",
    lambda v: Evidence(**v),
    (Field("source_id", STR), Field("paragraph", USIZE), Field("quote", STR)),
)
_DATA_ROW = Struct(
    "DataRow",
    lambda v: DataRow(**v),
    (Field("label", STR), Field("value", STR), Field("scope", STR), Field("evidence", _EVIDENCE)),
)
_ITEM = Struct(
    "Item",
    lambda v: Item(**v),
    (
        Field("headline", STR),
        Field("body", Seq(STR)),
        Field("source_ids", Seq(STR)),
        Field("evidence", Seq(_EVIDENCE)),
        Field("data_rows", Seq(_DATA_ROW), default=list),
    ),
)
_SECTION = Struct(
    "Section", lambda v: Section(**v), (Field("title", STR), Field("items", Seq(_ITEM)))
)
_SCHEDULE = Struct(
    "Schedule",
    lambda v: Schedule(**v),
    (
        Field("when", STR), Field("arrangement", STR), Field("source_id", STR),
        Field("paragraph", USIZE), Field("quote", STR),
    ),
)
_DRAFT = Struct(
    "Input",
    lambda v: Draft(**v),
    (
        Field("title", STR),
        Field("intro", STR),
        Field("sections", Seq(_SECTION)),
        Field("schedule", Seq(_SCHEDULE), default=list),
        Field("scope_note", compat.Opt(STR)),
    ),
)

_SPACES = str.maketrans({"\n": " ", "\r": " ", "\t": " "})
_ESCAPES = {"<": "&lt;", ">": "&gt;", "&": "&amp;"} | {c: "\\" + c for c in "\\`*_[]{}#|!~"}
_FORM_SAFE = frozenset(b"*-._0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")


def _text(value: str, limit: int) -> None:
    if validators.trim(value) == "" or len(value) > limit:
        raise BriefingError("Invalid briefing text length")
    if any(validators.is_control(c) and not validators.is_space(c) for c in value):
        raise BriefingError("Invalid briefing text")


def md(value: str) -> str:
    """Inline text that Markdown shows literally: no markup, HTML, images or line breaks."""
    return "".join(_ESCAPES.get(c, c) for c in value.translate(_SPACES))


def _get(value: Any, *keys: str) -> Any:
    """`value[k1][k2]...`, None for anything missing."""
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _form(pairs: Iterable[tuple[str, str]]) -> str:
    """`form_urlencoded::Serializer`: application/x-www-form-urlencoded, UTF-8."""

    def encode(text: str) -> str:
        return "".join(
            chr(b) if b in _FORM_SAFE else "+" if b == 0x20 else f"%{b:02X}"
            for b in text.encode()
        )

    return "&".join(f"{encode(k)}={encode(v)}" for k, v in pairs)


def _parse_url(value: str) -> ada_url.URL:
    try:
        return validators.whatwg(value)
    except ValueError:
        pass
    # Only the commonest reason is named; other reasons a URL fails to parse are not modeled.
    stripped = value.strip("".join(map(chr, range(0x21))))
    stripped = stripped.replace("\t", "").replace("\n", "").replace("\r", "")
    scheme = stripped.split(":", 1)[0] if ":" in stripped else ""
    if not (scheme[:1].isascii() and scheme[:1].isalpha()) or not all(
        c.isascii() and (c.isalnum() or c in "+-.") for c in scheme
    ):
        raise BriefingError("relative URL without a base") from None
    raise BriefingError("invalid URL") from None


def _material(snapshot: Any, id: str) -> tuple[dict[str, Any], ada_url.URL]:
    items = _get(snapshot, "materials")
    if not isinstance(items, list):
        raise BriefingError("Missing frozen materials")
    found = [item for item in items if isinstance(item, dict) and item.get("id") == id]
    if not found:
        raise BriefingError("Unknown briefing source")
    if len(found) > 1:
        raise BriefingError("Duplicate frozen source identity")
    item = found[0]
    paragraphs = _get(item, "content", "original", "paragraphs")
    if not isinstance(paragraphs, list) or not paragraphs:
        raise BriefingError("Briefing source has no original text")
    raw = item.get("url")
    if not isinstance(raw, str):
        raise BriefingError("Missing source URL")
    url = _parse_url(raw)
    if (
        url.protocol not in ("http:", "https:")
        or not url.hostname
        or url.username
        or url.password
    ):
        raise BriefingError("Unsafe briefing source URL")
    title = _get(item, "content", "title")
    if not isinstance(title, str):
        raise BriefingError("Missing source title")
    _text(title, 1000)
    return item, url


def _evidence(snapshot: Any, source: str, paragraph: int, quote: str) -> None:
    _text(quote, 4000)
    item, _ = _material(snapshot, source)
    paragraphs = item["content"]["original"]["paragraphs"]
    original = paragraphs[paragraph] if paragraph < len(paragraphs) else None
    if not isinstance(original, str):
        raise BriefingError("Unknown original paragraph")
    if quote not in original:
        raise BriefingError("Evidence does not occur in its cited source paragraph")


def _link(snapshot: Any, id: str, label: str | None) -> str:
    item, url = _material(snapshot, id)
    content = item["content"]
    publisher = content.get("publisher")
    if not isinstance(publisher, str) or validators.trim(publisher) == "":
        publisher = url.hostname
    title = f"{publisher}｜{content['title']}"
    href = url.href.replace("<", "%3C").replace(">", "%3E")
    return f"[{md(title if label is None else label)}](<{href}>)"


def _day(snapshot: Any, key: str) -> str:
    value = _get(snapshot, key)
    if not isinstance(value, str):
        raise BriefingError("Missing report window")
    if compat.format_date(compat.parse_date(value, spaces=False)) != value:
        raise BriefingError("Invalid report window")
    return value


def _check_item(snapshot: Any, item: Item) -> None:
    _text(item.headline, 200)
    if not 1 <= len(item.body) <= 8:
        raise BriefingError("Missing or excessive briefing body")
    if not 1 <= len(item.source_ids) <= 12:
        raise BriefingError("Invalid briefing sources")
    if not (1 <= len(item.evidence) <= 30 and len(item.data_rows) <= 12):
        raise BriefingError("Invalid briefing evidence")
    ids = set(item.source_ids)
    if len(ids) != len(item.source_ids):
        raise BriefingError("Duplicate briefing source")
    cited = set()
    for reference in [*item.evidence, *(row.evidence for row in item.data_rows)]:
        if reference.source_id not in ids:
            raise BriefingError("Evidence is outside this item's sources")
        _evidence(snapshot, reference.source_id, reference.paragraph, reference.quote)
        cited.add(reference.source_id)
    if ids != cited:
        raise BriefingError("Every item source needs evidence")
    for body in item.body:
        _text(body, 2000)


def render(input: Any, snapshot: Any) -> str:
    """The briefing's Markdown for the editorial draft `input` against `snapshot`.

    `input` is the decoded editorial JSON (dicts, lists, str, int):
    `{"title", "intro", "sections": [{"title", "items": [{"headline", "body":
    [str], "source_ids": [str], "evidence": [{"source_id", "paragraph", "quote"}],
    "data_rows"?: [{"label", "value", "scope", "evidence": {...}}]}]}],
    "schedule"?: [{"when", "arrangement", "source_id", "paragraph", "quote"}],
    "scope_note"?: str | None}`, unknown members refused. `snapshot` is the
    frozen media snapshot: `week_start`/`week_end` (YYYY-MM-DD), `plan.config.id`
    or `plan_id`, and `materials: [{"id", "url", "content": {"title",
    "publisher"?, "published_at"?, "original": {"paragraphs": [str]}}}]`.
    Every quote must occur in the cited paragraph (zero-based) of its source.
    Raises `BriefingError`, or `DecodeError` for a malformed draft.
    """
    if len(jsonfmt.dumps(input).encode()) > MAX_DRAFT:
        raise BriefingError("Briefing draft is too large")
    draft: Draft = compat.decode(_DRAFT, input, streamed=False)
    _text(draft.title, 200)
    _text(draft.intro, 2000)
    if not 1 <= len(draft.sections) <= 12:
        raise BriefingError("Invalid briefing sections")
    if sum(len(section.items) for section in draft.sections) > 40:
        raise BriefingError("Too many briefing items")
    if len(draft.schedule) > 30:
        raise BriefingError("Too many schedule rows")
    start, end = _day(snapshot, "week_start"), _day(snapshot, "week_end")
    if compat.parse_date(start, spaces=False) > compat.parse_date(end, spaces=False):
        raise BriefingError("Invalid report window")
    out = [f"# {md(draft.title)}\n\n> {start} — {end}\n\n{md(draft.intro)}\n\n## 本周速览\n\n"]
    for section in draft.sections:
        _text(section.title, 100)
        if not section.items:
            raise BriefingError("Empty briefing section")
        for item in section.items:
            _check_item(snapshot, item)
            out.append(f"- {_link(snapshot, item.source_ids[0], item.headline)}\n")
    for section in draft.sections:
        out.append(f"\n## {md(section.title)}\n\n")
        for item in section.items:
            out.append(f"### {_link(snapshot, item.source_ids[0], item.headline)}\n\n")
            out.extend(f"{md(body)}\n\n" for body in item.body)
            if item.data_rows:
                out.append("| 已披露数据 | 数值 | 统计期与范围 |\n| --- | --- | --- |\n")
                for row in item.data_rows:
                    _text(row.label, 200)
                    _text(row.value, 200)
                    _text(row.scope, 500)
                    out.append(f"| {md(row.label)} | {md(row.value)} | {md(row.scope)} |\n")
                out.append("\n")
            sources = []
            for id in item.source_ids:
                source, _ = _material(snapshot, id)
                published = _get(source, "content", "published_at")
                date = (
                    f"（发布于 {md(published.split('T', 1)[0])}）"
                    if isinstance(published, str) and published
                    else ""
                )
                sources.append(f"{_link(snapshot, id, None)}{date}")
            out.append(f"来源：{'；'.join(sources)}\n\n")
    if draft.schedule:
        out.append("## 已公布的后续安排\n\n| 时间 | 安排 | 来源 |\n| --- | --- | --- |\n")
        for entry in draft.schedule:
            _text(entry.when, 100)
            _text(entry.arrangement, 1000)
            _evidence(snapshot, entry.source_id, entry.paragraph, entry.quote)
            link = _link(snapshot, entry.source_id, None)
            out.append(f"| {md(entry.when)} | {md(entry.arrangement)} | {link} |\n")
        out.append("\n")
    if draft.scope_note is not None:
        _text(draft.scope_note, 2000)
        out.append(f"{md(draft.scope_note)}\n\n")
    plan = _get(snapshot, "plan", "config", "id")
    if not isinstance(plan, str):
        plan = _get(snapshot, "plan_id")
    if not isinstance(plan, str):
        raise BriefingError("Missing parent search plan")
    _text(plan, 200)
    query = _form([("view", "materials"), ("source", f"search:{plan}")])
    out.append(f"---\n\n[查看本专题全部采集材料](/sources?{query})\n")
    return "".join(out)
