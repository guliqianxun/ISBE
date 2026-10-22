"""The research manifest, frozen bundle and model extraction records.

Field order is the declaration order: `manifest.json` is these
records serialized in that order, and its SHA-256 is part of the report
fingerprint, so the order must not change. The operator's manifest and the
model's extraction reject unknown members; a frozen
bundle tolerates them outside its sources and scope.
"""

from dataclasses import dataclass
from typing import Any

from isbe.reports.research_compat import STR, USIZE, Field, Opt, Seq, Struct

_STRS = Seq(STR)


@dataclass
class Scope:
    from_: str
    to: str
    region: str
    languages: list[str]
    questions: list[str]

    def to_json(self) -> dict[str, Any]:
        return {
            "from": self.from_,
            "to": self.to,
            "region": self.region,
            "languages": self.languages,
            "questions": self.questions,
        }


@dataclass
class Excerpt:
    id: str
    location: str
    quote: str

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "location": self.location, "quote": self.quote}


@dataclass
class Source:
    id: str
    title: str
    publisher: str
    url: str
    material_type: str
    published_on: str | None
    coverage: str
    reading_depth: str
    role: str
    limitations: list[str]
    original_file: str
    sha256: str
    text_file: str
    excerpts: list[Excerpt]

    def to_json(self) -> dict[str, Any]:
        out = {name: getattr(self, name) for name in _SOURCE_FIELDS}
        out["excerpts"] = [e.to_json() for e in self.excerpts]
        return out


@dataclass
class Input:
    topic: str
    title: str
    period: str
    scope: Scope
    search_note: str
    limitations: list[str]
    sources: list[Source]


@dataclass
class Position:
    id: str
    start: int
    end: int

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "start": self.start, "end": self.end}


@dataclass
class FrozenSource:
    source: Source
    mime: str
    text_sha256: str
    positions: list[Position]

    def to_json(self) -> dict[str, Any]:
        return {
            "source": self.source.to_json(),
            "mime": self.mime,
            "text_sha256": self.text_sha256,
            "positions": [p.to_json() for p in self.positions],
        }


@dataclass
class Bundle:
    topic: str
    title: str
    period: str
    scope: Scope
    search_note: str
    limitations: list[str]
    input_sha256: str
    sources: list[FrozenSource]

    def to_json(self) -> dict[str, Any]:
        """Declaration order: serialize with `jsonfmt.dumps(..., sort_keys=False)`."""
        return {
            "topic": self.topic,
            "title": self.title,
            "period": self.period,
            "scope": self.scope.to_json(),
            "search_note": self.search_note,
            "limitations": self.limitations,
            "input_sha256": self.input_sha256,
            "sources": [s.to_json() for s in self.sources],
        }


@dataclass
class Span:
    evidence: str
    text: str


@dataclass
class ExtractedSource:
    source_id: str
    subjects: list[Span]
    objects: list[Span]
    times: list[Span]
    actions: list[Span]
    figures: list[Span]


@dataclass
class Draft:
    sources: list[ExtractedSource]


# Extraction groups in report order, with their report headings.
GROUPS = (
    ("subjects", "主体与职责线索"),
    ("objects", "对象线索"),
    ("times", "时间线索"),
    ("actions", "行动、要求与安排原文"),
    ("figures", "数据与指标原文"),
)

_SOURCE_FIELDS = (
    "id", "title", "publisher", "url", "material_type", "published_on", "coverage",
    "reading_depth", "role", "limitations", "original_file", "sha256", "text_file",
)


def _strings(*names: str) -> tuple[Field, ...]:
    return tuple(Field(name, STR) for name in names)


SCOPE = Struct(
    "Scope",
    lambda v: Scope(v["from"], v["to"], v["region"], v["languages"], v["questions"]),
    (*_strings("from", "to", "region"), Field("languages", _STRS), Field("questions", _STRS)),
)
EXCERPT = Struct("Excerpt", lambda v: Excerpt(**v), _strings("id", "location", "quote"))
SOURCE = Struct(
    "Source",
    lambda v: Source(**v),
    (
        *_strings("id", "title"),
        Field("publisher", STR, default=str),
        *_strings("url", "material_type"),
        Field("published_on", Opt(STR)),
        *_strings("coverage", "reading_depth", "role"),
        Field("limitations", _STRS),
        *_strings("original_file", "sha256", "text_file"),
        Field("excerpts", Seq(EXCERPT)),
    ),
)
INPUT = Struct(
    "Input",
    lambda v: Input(**v),
    (
        *_strings("topic", "title", "period"),
        Field("scope", SCOPE),
        Field("search_note", STR),
        Field("limitations", _STRS),
        Field("sources", Seq(SOURCE)),
    ),
)
POSITION = Struct(
    "Position",
    lambda v: Position(**v),
    (Field("id", STR), Field("start", USIZE), Field("end", USIZE)),
    deny_unknown=False,
)
FROZEN_SOURCE = Struct(
    "FrozenSource",
    lambda v: FrozenSource(**v),
    (Field("source", SOURCE), *_strings("mime", "text_sha256"), Field("positions", Seq(POSITION))),
    deny_unknown=False,
)
BUNDLE = Struct(
    "Bundle",
    lambda v: Bundle(**v),
    (
        *_strings("topic", "title", "period"),
        Field("scope", SCOPE),
        Field("search_note", STR),
        Field("limitations", _STRS),
        Field("input_sha256", STR),
        Field("sources", Seq(FROZEN_SOURCE)),
    ),
    deny_unknown=False,
)
SPAN = Struct("Span", lambda v: Span(**v), _strings("evidence", "text"))
_SPANS = Seq(SPAN)
EXTRACTED_SOURCE = Struct(
    "ExtractedSource",
    lambda v: ExtractedSource(**v),
    (Field("source_id", STR), *(Field(name, _SPANS) for name, _ in GROUPS)),
)
DRAFT = Struct("Draft", lambda v: Draft(**v), (Field("sources", Seq(EXTRACTED_SOURCE)),))
