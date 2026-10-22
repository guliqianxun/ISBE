"""Fixed-source research reports; model spans are indexes, not conclusions.

`run` freezes an operator-prepared manifest of sources (originals, their text
and the excerpts to quote) under `research/<topic>/<period>/`, asks the model
only to locate literal spans inside those excerpts, and writes
`reports/<topic>/<period>.md` showing every excerpt in full. Spans that are
not literal substrings of their excerpt are dropped; an unusable extraction
leaves the report without spans rather than without sources. Reports are
write-once: a period already taken by any report kind, or an existing report
file, is refused.

`evidence` and `original` (research_evidence.py) serve the frozen bundle.
"""

import os
import sqlite3
from pathlib import Path
from typing import Any

from isbe.core import assets, db, jsonfmt, validators
from isbe.core.llm import Llm, Prompt, Tier
from isbe.reports import research_compat as compat
from isbe.reports import research_uri
from isbe.reports.research_evidence import evidence, original
from isbe.reports.research_files import (
    MAX_MANIFEST,
    MAX_ORIGINAL,
    MAX_TEXT,
    ResearchError,
    child,
    extension,
    identifier,
    mkdir,
    os_error,
    read,
    sha256,
    write_new,
)
from isbe.reports.research_render import render
from isbe.reports.research_schema import (
    DRAFT,
    GROUPS,
    INPUT,
    Bundle,
    Draft,
    ExtractedSource,
    FrozenSource,
    Input,
    Position,
)

__all__ = ["ResearchError", "evidence", "original", "run"]

type SourceFiles = list[tuple[str, bytes]]


def _text(value: str, limit: int) -> None:
    if validators.trim(value) == "" or validators.byte_len(value) > limit:
        raise ResearchError("empty or oversized research text")
    if any(validators.is_control(c) and c not in "\n\r\t" for c in value):
        raise ResearchError("unsupported control character")


def _strings(values: list[str], max_count: int) -> None:
    if len(values) > max_count:
        raise ResearchError("too many research entries")
    for value in values:
        _text(value, 4000)


def _source_url(value: str) -> None:
    try:
        scheme = research_uri.scheme(value)
    except research_uri.UriError as error:
        raise ResearchError("invalid source URL") from error
    # An absolute URI always has an authority, so the host check always holds here.
    if scheme not in ("http", "https") or "@" in value:
        raise ResearchError("source URL must be HTTP(S) without credentials")


def _decode_input(raw: bytes) -> Input:
    try:
        parsed: Input = compat.decode(INPUT, compat.loads(compat.utf8(raw)), streamed=True)
    except (ValueError, RecursionError) as error:
        raise ResearchError("invalid research input JSON") from error
    return parsed


def _original_kind(name: str, raw: bytes) -> tuple[str, str]:
    """The frozen extension and MIME type of an original, which must be what its name says."""
    match extension(name):
        case "pdf" if raw.startswith(b"%PDF-"):
            return "pdf", "application/pdf"
        case "html" | "htm":
            compat.utf8(raw)
            return "html", "text/html; charset=utf-8"
        case "txt":
            compat.utf8(raw)
            return "txt", "text/plain; charset=utf-8"
    raise ResearchError("research originals must be PDF, UTF-8 HTML or text")


def _char_len(lead: int) -> int:
    return 1 if lead < 0x80 else 2 if lead < 0xE0 else 3 if lead < 0xF0 else 4


def prepare(input: Path) -> tuple[Bundle, SourceFiles]:
    """Validate the manifest at `input` and its files; nothing is written or sent."""
    raw = read(input, MAX_MANIFEST)
    parsed = _decode_input(raw)
    if not identifier(parsed.topic) or not identifier(parsed.period):
        raise ResearchError("invalid research topic or period")
    _text(parsed.title, 300)
    _text(parsed.scope.region, 300)
    start = compat.parse_date(parsed.scope.from_)
    end = compat.parse_date(parsed.scope.to)
    if start > end:
        raise ResearchError("research window is reversed")
    if not parsed.scope.languages or not parsed.scope.questions:
        raise ResearchError("research languages and questions are required")
    _strings(parsed.scope.languages, 8)
    _strings(parsed.scope.questions, 16)
    _strings(parsed.limitations, 32)
    _text(parsed.search_note, 4000)
    if not 1 <= len(parsed.sources) <= 16:
        raise ResearchError("research requires 1 to 16 sources")
    root = input.parent
    sources: list[FrozenSource] = []
    files: SourceFiles = [("input.json", raw)]
    source_ids: set[str] = set()
    evidence_ids: set[str] = set()
    for source in parsed.sources:
        if not identifier(source.id) or source.id in source_ids:
            raise ResearchError("invalid or repeated source id")
        source_ids.add(source.id)
        for value in (
            source.title, source.material_type, source.coverage, source.reading_depth,
            source.role,
        ):
            _text(value, 4000)
        _strings(source.limitations, 32)
        if source.publisher:
            _text(source.publisher, 300)
        _source_url(source.url)
        if source.published_on is not None:
            try:
                compat.parse_date(source.published_on)
            except compat.DecodeError as error:
                raise ResearchError("invalid publication date") from error
        original_raw = read(child(root, source.original_file), MAX_ORIGINAL)
        if sha256(original_raw) != source.sha256:
            raise ResearchError("original source hash mismatch")
        ext, mime = _original_kind(source.original_file, original_raw)
        body = read(child(root, source.text_file), MAX_TEXT)
        _text(compat.utf8(body), MAX_TEXT)
        if not source.excerpts:
            raise ResearchError("every source needs at least one excerpt")
        positions = []
        for excerpt in source.excerpts:
            if not identifier(excerpt.id) or excerpt.id in evidence_ids:
                raise ResearchError("invalid or repeated evidence id")
            evidence_ids.add(excerpt.id)
            if len(evidence_ids) > 64:
                raise ResearchError("at most 64 excerpts are supported")
            _text(excerpt.location, 1000)
            _text(excerpt.quote, 12000)
            # Byte offsets: a UTF-8 needle only matches on
            # character boundaries of a UTF-8 haystack.
            quote = excerpt.quote.encode()
            at = body.find(quote)
            if at < 0:
                raise ResearchError("excerpt not found in source text")
            if body.find(quote, at + _char_len(body[at])) >= 0:
                raise ResearchError("excerpt is ambiguous in source text")
            positions.append(Position(excerpt.id, at, at + len(quote)))
        source.original_file = f"{source.id}.original.{ext}"
        source.text_file = f"{source.id}.text.txt"
        files.append((source.original_file, original_raw))
        files.append((source.text_file, body))
        sources.append(FrozenSource(source, mime, sha256(body), positions))
    bundle = Bundle(
        topic=parsed.topic,
        title=parsed.title,
        period=parsed.period,
        scope=parsed.scope,
        search_note=parsed.search_note,
        limitations=parsed.limitations,
        input_sha256=sha256(raw),
        sources=sources,
    )
    return bundle, files


def model_input(bundle: Bundle) -> str:
    """The prompt's user message: only ids and excerpts, never paths or metadata."""
    sources = [
        {"id": f.source.id, "excerpts": [e.to_json() for e in f.source.excerpts]}
        for f in bundle.sources
    ]
    user = jsonfmt.dumps({"sources": sources})
    if validators.byte_len(user) > MAX_TEXT:
        raise ResearchError("research prompt exceeds 200 KiB")
    return user


def validate_draft(draft: Draft, bundle: Bundle) -> int:
    """Drop spans that are not literal text of their cited excerpt; returns how many.

    A group of more than 8 spans is dropped whole. A span citing an excerpt of
    another source, an empty span or an unknown source rejects the extraction.
    """
    if len(draft.sources) != len(bundle.sources):
        raise ResearchError("extraction must include every source")
    seen: set[str] = set()
    rejected = 0
    for item in draft.sources:
        if item.source_id in seen:
            raise ResearchError("duplicate extracted source")
        seen.add(item.source_id)
        frozen = next((s for s in bundle.sources if s.source.id == item.source_id), None)
        if frozen is None:
            raise ResearchError("unknown extracted source")
        excerpts = frozen.source.excerpts
        for field, _ in GROUPS:
            spans = getattr(item, field)
            if len(spans) > 8:
                rejected += len(spans)
                spans.clear()
                continue
            for span in spans:
                _text(span.text, 500)
                if not any(e.id == span.evidence for e in excerpts):
                    raise ResearchError("model cited unknown evidence for this source")
            kept = [
                span for span in spans
                if any(e.id == span.evidence and span.text in e.quote for e in excerpts)
            ]
            rejected += len(spans) - len(kept)
            spans[:] = kept
    return rejected


def _extract(text: str, bundle: Bundle) -> tuple[Draft, int]:
    try:
        draft: Draft = compat.decode(DRAFT, compat.loads(text), streamed=True)
    except (ValueError, RecursionError) as error:
        raise ResearchError("model research output must be JSON") from error
    return draft, validate_draft(draft, bundle)


def _empty(bundle: Bundle) -> Draft:
    return Draft([ExtractedSource(s.source.id, [], [], [], [], []) for s in bundle.sources])


def run(
    conn: sqlite3.Connection,
    llm: Llm,
    run_id: str,
    data: Path,
    input: Path,
    stats: dict[str, Any],
) -> None:
    """Generate the research report described by the manifest at `input`.

    Called inside `db.job(conn, "research", None, ...)`, as the CLI does:
    `db.job(conn, "research", None, lambda run, stats: research.run(conn, llm,
    run, data, input, stats))`. Fills `stats` with `report` (the data-relative
    path), `materials`, `evidence`, `llm_call`, `rejected_spans` and
    `extraction_error` (None or the reason spans were discarded). Raises
    `ResearchError` for an unusable manifest or a taken period (`DecodeError`
    for a bad date or non-UTF-8 text; both are `ValueError`s whose
    `errors.describe` gives the message), and the `LlmError`/`Refused` of a
    failed model call. The frozen bundle stays in place
    after a failed call, so the period cannot silently repeat a model request.
    """
    bundle, files = prepare(input)
    row = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM reports WHERE topic_id=? AND period=?)",
        (bundle.topic, bundle.period),
    ).fetchone()
    if row[0]:
        raise ResearchError("this report period already exists; choose a new period")
    rel = f"reports/{bundle.topic}/{bundle.period}.md"
    path = child(data, rel)
    if os.path.exists(path):
        raise ResearchError("this report file already exists; choose a new period")
    user = model_input(bundle)
    manifest = jsonfmt.dumps(bundle.to_json(), sort_keys=False).encode()
    if len(manifest) > MAX_MANIFEST:
        raise ResearchError("frozen manifest exceeds its size limit")
    parent = child(data, f"research/{bundle.topic}")
    mkdir(parent)
    frozen = child(parent, bundle.period)
    # Preserve uncertain/failed attempts so rerunning cannot silently repeat a model request.
    try:
        os.mkdir(frozen)
    except OSError as error:
        raise ResearchError(
            "research period already exists or cannot be reserved"
        ) from os_error(error)
    for name, content in files:
        write_new(child(frozen, name), content)
    write_new(child(frozen, "manifest.json"), manifest)
    reply = llm.complete(
        conn,
        run_id,
        Prompt(
            purpose="research",
            tier=Tier.SMART,
            system=assets.text("prompts", "zh", "research.md"),
            user=user,
            max_tokens=6000,
        ),
    )
    if validators.byte_len(reply.text) > MAX_TEXT:
        raise ResearchError("model research output exceeds its size limit")
    # Model annotations must not veto delivery of the source material.
    extraction_error: str | None = None
    try:
        draft, rejected = _extract(reply.text, bundle)
    except ResearchError as error:
        draft, rejected, extraction_error = _empty(bundle), 0, str(error)
    # shortcut: literal spans cannot verify roles; keep full quotations until human review is recorded.
    report = render(bundle, draft)
    mkdir(path.parent)
    write_new(path, report.encode())
    fingerprint = {
        "mode": "source_extraction",
        "rejected_spans": rejected,
        "extraction_error": extraction_error,
        "materials": [s.source.id for s in bundle.sources],
        "llm_call": reply.call_id,
        "input_sha256": bundle.input_sha256,
        "research_manifest_sha256": sha256(manifest),
        "text_sha256": {s.source.id: s.text_sha256 for s in bundle.sources},
    }
    conn.execute(
        "INSERT INTO reports(id,topic_id,period,kind,path,fingerprint,created_at) "
        "VALUES(?,?,?,'research',?,?,?)",
        (
            f"{bundle.topic}/{bundle.period}/research", bundle.topic, bundle.period, rel,
            jsonfmt.dumps(fingerprint), db.now(),
        ),
    )
    stats["report"] = rel
    stats["materials"] = len(bundle.sources)
    stats["evidence"] = sum(len(s.positions) for s in bundle.sources)
    stats["llm_call"] = reply.call_id
    stats["rejected_spans"] = rejected
    stats["extraction_error"] = extraction_error
