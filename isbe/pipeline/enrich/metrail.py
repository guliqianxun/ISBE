"""Full text from PDFs parsed by metrail, the LAN PDF-to-text service.

All papers are uploaded first so metrail's workers parse in parallel, then
polled until each is done. A paper whose text is in but whose figure is not
yet looked for comes back here: metrail answers its re-upload with the
document it already parsed.
"""

import logging
import sqlite3
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from isbe.core import db, errors, http, jsonfmt, paths, validators
from isbe.core.validators import trim
from isbe.topics import Metrail

from .calls import BodyLimit, EnrichError, call, header, read, step
from .figures import MAX_FIGURE, figure_rank, write
from .text import collapse

log = logging.getLogger(__name__)

MAX_PDF = 200 << 20
BOUNDARY = "isbe-form-boundary-5f0c1e9a7b2d4c38"
TIMEOUT = 300
_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


@dataclass(frozen=True)
class Atom:
    page: int | None
    kind: str
    text: str | None
    metadata: Any
    image_url: str | None


def _optional(item: dict[str, Any], key: str, kind: type) -> Any:
    value = item.get(key)
    if value is not None and (not isinstance(value, kind) or isinstance(value, bool)):
        raise ValueError(f"invalid type for field `{key}`")
    return value


def atoms(value: Any) -> list[Atom]:
    """A list of atoms: `kind` required, the rest optional, unknown fields ignored."""
    if not isinstance(value, list):
        raise ValueError("invalid type: expected a sequence")
    out = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("invalid type: expected struct Atom")
        if "kind" not in item:
            raise ValueError("missing field `kind`")
        page = _optional(item, "page", int)
        if page is not None and not -(1 << 63) <= page < (1 << 63):
            raise ValueError("invalid value for field `page`")
        kind = item["kind"]
        if not isinstance(kind, str):
            raise ValueError("invalid type for field `kind`")
        out.append(Atom(page, kind, _optional(item, "text", str), item.get("metadata"),
                        _optional(item, "image_url", str)))
    return out


def _field(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def submit(client: http.Http, base: str, cfg: Metrail, essaies: str | None, id: str,
           extra: Any) -> str:
    """Fetch the paper's PDF and upload it; returns metrail's document id."""
    arxiv = id.removeprefix("arxiv:") if id.startswith("arxiv:") else None
    path = _field(extra, "path")
    if essaies is not None and isinstance(path, str):
        url, params = f"{essaies.rstrip('/')}/api/file", {"path": path}
    elif arxiv is not None:
        url, params = f"https://arxiv.org/pdf/{arxiv}", None
    else:
        raise EnrichError("no PDF to fetch")
    with ExitStack() as stack:
        with step("download paper PDF"):
            response = stack.enter_context(call(client, "GET", url, params=params))
        with step("read downloaded paper PDF"):
            pdf = read(response, MAX_PDF)
    if not pdf.startswith(b"%PDF-"):
        raise EnrichError("the PDF download is not a PDF")
    name = f"{(arxiv if arxiv is not None else id).replace('/', '_').replace(':', '_')}.pdf"
    fields = [
        ("backend", cfg.backend),
        ("ocr", "true" if cfg.ocr else "false"),
        ("table_backend", cfg.table_backend),
        ("figure_backend", cfg.figure_backend),
    ]
    form = bytearray()
    for key, value in fields:
        if value is not None:
            form += (f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'
                     f"{value}\r\n").encode()
    form += (f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
             "Content-Type: application/pdf\r\n\r\n").encode()
    form += pdf
    form += f"\r\n--{BOUNDARY}--\r\n".encode()
    with ExitStack() as stack:
        with step("submit PDF to metrail"):
            response = stack.enter_context(call(
                client, "POST", f"{base}/api/documents", content=bytes(form),
                headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"},
            ))
        with step("read metrail submission response"):
            meta = jsonfmt.loads(read(response, http.MAX_BODY))
    doc = _field(meta, "id")
    if not isinstance(doc, str):
        raise EnrichError("metrail answered without a document id")
    return doc


def finished(client: http.Http, base: str, doc: str) -> list[Atom] | None:
    """The document's atoms once metrail is done with it, None while it works."""
    with ExitStack() as stack:
        with step("read metrail document status"):
            response = stack.enter_context(call(client, "GET", f"{base}/api/documents/{doc}"))
        with step("decode metrail document status"):
            meta = jsonfmt.loads(read(response, http.MAX_BODY))
    state = _field(meta, "state")
    if state == "error":
        message = _field(meta, "error")
        raise EnrichError(message if isinstance(message, str) else "parse failed")
    if state != "done":
        return None
    with ExitStack() as stack:
        with step("fetch metrail document atoms"):
            response = stack.enter_context(
                call(client, "GET", f"{base}/api/documents/{doc}/atoms"))
        with step("decode metrail document atoms"):
            return atoms(jsonfmt.loads(read(response, MAX_PDF)))


def pick_figure(items: list[Atom]) -> Atom | None:
    """The paper's method diagram: the best-ranked figure with a detected caption."""
    figures = [
        a for a in items
        if a.kind == "figure" and a.image_url is not None
        and _field(a.metadata, "caption_detected") == "yes"
    ]
    return min(figures, key=lambda a: figure_rank(a.text or ""), default=None)


def figure_url(base: str, path: str) -> str:
    """`{base}{path}`, refused unless it stays on metrail itself:
    the path comes from metrail's answer, and `@host` or `\\host` in it would
    otherwise move the request elsewhere. The WHATWG form is what gets sent, so
    the HTTP client cannot read the authority differently."""
    try:
        target, origin = validators.whatwg(f"{base}{path}"), validators.whatwg(base)
    except ValueError:
        raise EnrichError("unsupported figure URL") from None
    if target.origin != origin.origin or target.username or target.password:
        raise EnrichError("unsupported figure URL")
    return target.href


def save_figure(client: http.Http, base: str, data: Path, id: str,
                items: list[Atom]) -> dict[str, Any]:
    """Download the picked figure to `figures/<id>.<ext>`; `{"file", "caption"}`, or
    `{}` when the paper has no usable figure."""
    atom = pick_figure(items)
    if atom is None or atom.image_url is None:
        return {}
    url = figure_url(base, atom.image_url)
    # metrail serves a downscaled copy for ?w=.
    with call(client, "GET", url, params={"w": "960"}) as response:
        content_type = header(response, "content-type")
        # Compare the media type, not its parameters
        # (`image/png; charset=binary`); a refusal here re-uploads the PDF every run.
        ext = _EXTENSIONS.get(trim(content_type.split(";")[0])) if content_type else None
        if ext is None:
            shown = "None" if content_type is None else f"Some({jsonfmt.dumps(content_type)})"
            raise EnrichError(f"the figure is {shown}, not an image")
        try:
            image = read(response, MAX_FIGURE)
        except BodyLimit:
            return {}
    stem = id.removeprefix("arxiv:").replace("/", "_").replace(":", "_")
    file = f"figures/{stem}.{ext}"
    path = data / file
    paths.check_path(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    with step(f"write {path}"):
        write(path, image)
    return {"file": file, "caption": collapse(atom.text or "")[:300]}


def body(items: list[Atom]) -> str:
    """The paper as markdown: a `## Page N` heading per page, its text, then each
    table as a block that starts with `### ` and its caption. Figures are left
    out; their captions are part of the page text."""
    out: list[str] = []
    page: int | None = None
    prev = ""
    for a in items:
        if a.page != page:
            page, prev = a.page, ""
            out.append(f"## Page {0 if a.page is None else a.page + 1}\n\n")
        text = trim(a.text or "")
        if a.kind == "text":
            if prev:
                out[-1] = out[-1][:-1]  # chunks split at line breaks: continue the page's text
            out.append(strip_overlap(prev, text))
            out.append("\n\n")
            prev = text
        elif a.kind == "table" and text:
            prev = ""
            caption = _field(a.metadata, "caption")
            heading = collapse(caption) if isinstance(caption, str) else "Table"
            out.append(f"### {heading}\n{text}\n\n")
    return "".join(out)


def strip_overlap(prev: str, next: str) -> str:
    """metrail chunks a page with overlap: a chunk starts with the tail of the
    previous one, then a line break. Drop the longest run of `next`'s leading
    lines that `prev` ends with."""
    i = next.rfind("\n")
    while i >= 0:
        if prev.endswith(next[:i]):
            return next[i + 1:]
        i = next.rfind("\n", 0, i)
    return next


type Sleep = Callable[[float], None]


def fetch(conn: sqlite3.Connection, base: str, cfg: Metrail, data: Path, essaies: str | None,
          papers: list[tuple[str, str]], failed: dict[str, Any],
          *, client: http.Http | None = None, sleep: Sleep = time.sleep) -> tuple[int, int]:
    """Parse the PDFs of `papers` (id, extra) into `fulltext` and `figures/`;
    returns how many texts and figures landed."""
    if client is None:
        with http.Http(TIMEOUT) as owned:
            return fetch(conn, base, cfg, data, essaies, papers, failed, client=owned, sleep=sleep)
    pending: list[tuple[str, str]] = []
    for id, extra in papers:
        parsed = jsonfmt.loads(extra)
        try:
            pending.append((id, submit(client, base, cfg, essaies, id, parsed)))
        except Exception as error:
            failed[id] = errors.describe(error)
    extracted = figures = 0
    progress = time.monotonic()
    while pending:
        waiting = []
        for id, doc in pending:
            try:
                result = finished(client, base, doc)
            except Exception as error:
                failed[id] = f"metrail document {doc}: {errors.describe(error)}"
                progress = time.monotonic()
                continue
            if result is None:
                waiting.append((id, doc))
                continue
            # A figure that fails to download is looked for again next run.
            figure: dict[str, Any] | None
            try:
                figure = save_figure(client, base, data, id, result)
            except Exception as error:
                log.warning("%s: no figure this run: %s", id, errors.describe(error))
                figure = None
            figures += figure is not None and "file" in figure
            conn.execute(
                "INSERT OR REPLACE INTO fulltext (item_id, via, body, figure, extracted_at) "
                "VALUES (?, 'metrail', ?, ?, ?)",
                (id, body(result), None if figure is None else jsonfmt.dumps(figure), db.now()),
            )
            extracted += 1
            progress = time.monotonic()
        pending = waiting
        if pending and int(time.monotonic() - progress) > cfg.poll_timeout_s:
            for id, doc in pending:
                failed[id] = f"metrail document {doc}: none finished for {cfg.poll_timeout_s}s"
            pending = []
        if pending:
            sleep(3)
    return extracted, figures
