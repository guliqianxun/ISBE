"""Deterministic text from arXiv's HTML papers; the cards
pass then extracts facts and verifies quotations against this stored text.

Only `https://arxiv.org/html/<id>` is fetched, with the id checked so it cannot
change the host or path, and only images under `https://arxiv.org/html/` are
ever downloaded: a figure `src` comes from the page and is resolved against it,
then checked again by the downloader.
"""

from contextlib import ExitStack
from dataclasses import dataclass, field

import ada_url

from isbe.core import http, validators
from isbe.core.validators import ascii_lower, byte_len, is_control, trim

from .calls import EnrichError, call, header, read_text, step
from .html_extract import MAX_FIGURES, MAX_HTML, Extract

ARXIV_HTML = "https://arxiv.org/html/"


@dataclass
class Figure:
    images: list[str] = field(default_factory=list)
    caption: str = ""


@dataclass
class Paper:
    body: str
    figures: list[Figure]
    figure_warnings: list[str]


def fetch(client: http.Http, item_id: str) -> Paper:
    id = arxiv_id(item_id)
    if id is None:
        raise EnrichError("invalid arXiv paper id")
    with ExitStack() as stack:
        with step("fetch arXiv HTML"):
            response = stack.enter_context(call(client, "GET", f"{ARXIV_HTML}{id}"))
        kind = header(response, "content-type")
        if kind is None or trim(kind.split(";")[0]) not in ("text/html", "application/xhtml+xml"):
            raise EnrichError("arXiv did not return an HTML paper")
        base = str(response.url)
        html = read_text(response, MAX_HTML)
    paper = parse(html)
    paper.figures = resolve_figures(base, paper.figures, paper.figure_warnings)
    return paper


def _digits(s: str) -> bool:
    return bool(s) and all("0" <= c <= "9" for c in s)


def arxiv_id(item_id: str) -> str | None:
    """The arXiv id of `arxiv:<id>` when it is a well-formed new- or old-style id."""
    if not item_id.startswith("arxiv:"):
        return None
    id = item_id.removeprefix("arxiv:")
    if byte_len(id) > 80:
        return None
    stem = id
    head, v, version = id.rpartition("v")
    if v and _digits(version):
        if int(version) > 0xFFFFFFFF or int(version) == 0:
            return None
        stem = head
    category, slash, number = stem.partition("/")
    if slash:
        if (
            not category
            or not ("a" <= ascii_lower(category[0]) <= "z")
            or not all(c.isascii() and (c.isalnum() or c in "-.") for c in category)
            or ".." in category
            or len(number) != 7
            or not _digits(number)
        ):
            return None
        date = number[:4]
    else:
        date, dot, number = stem.partition(".")
        if not dot or len(date) != 4 or not _digits(date) or len(number) not in (4, 5):
            return None
        if not _digits(number):
            return None
    return id if 1 <= int(date[2:]) <= 12 else None


def parse(html: str) -> Paper:
    if byte_len(html) > MAX_HTML:
        raise EnrichError("arXiv HTML exceeds 10 MiB")
    parsed = Extract()
    parsed.feed(html)
    parsed.close()
    if parsed.overflow:
        raise EnrichError("arXiv HTML exceeds extraction limits")
    if not parsed.finished or parsed.characters == 0:
        raise EnrichError("arXiv HTML has no complete, nonempty paper article")
    figures: list[Figure] = []
    warnings: list[str] = []
    for index, candidate in enumerate(parsed.figures):
        reason = candidate.invalid
        if reason is None and not candidate.images:
            reason = "no usable images"
        elif reason is None and not candidate.caption:
            reason = "missing caption"
        if reason is not None:
            warnings.append(f"figure group {index + 1} omitted: {reason}")
        else:
            figures.append(Figure(candidate.images, candidate.caption))
    if parsed.figure_limit_reached:
        warnings.append(f"additional figure groups omitted after the {MAX_FIGURES}-group limit")
    return Paper(trim(parsed.text), figures, warnings)


def _allowed(url: ada_url.URL) -> bool:
    path = ascii_lower(url.pathname)
    return (
        url.protocol == "https:"
        and url.hostname == "arxiv.org"
        and url.port == ""  # the default, 443: an explicit default port is normalized away
        and url.username == ""
        and url.password == ""
        and path.startswith("/html/")
        and "%2f" not in path
        and "%5c" not in path
    )


def image_url(base: str, src: str) -> str | None:
    """`src` resolved against `base` when both stay under arXiv's HTML tree and it
    names a PNG, JPEG or WebP; the downloader repeats this check."""
    if not src or byte_len(src) > 4096 or any(is_control(c) for c in src):
        return None
    try:
        page = validators.whatwg(base)
        if not _allowed(page):
            return None
        url = validators.whatwg(src, base)
    except ValueError:
        return None
    extension = ascii_lower(url.pathname.rsplit(".", 1)[-1])
    if not _allowed(url) or extension not in ("png", "jpg", "jpeg", "webp"):
        return None
    url.hash = ""
    return url.href


def resolve_figures(base: str, figures: list[Figure], warnings: list[str]) -> list[Figure]:
    """Absolute panel URLs; a group with any unusable panel is left out whole."""
    resolved = []
    for index, figure in enumerate(figures):
        images = [image_url(base, src) for src in figure.images]
        if any(image is None for image in images):
            warnings.append(
                f"image candidate {index + 1} omitted: unsupported or unsafe image URL"
            )
            continue
        resolved.append(Figure([i for i in images if i is not None], figure.caption))
    return resolved
