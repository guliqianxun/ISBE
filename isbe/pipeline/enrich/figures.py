"""Method figures of arXiv HTML papers, saved under `<data>/figures/`.

A paper's figure is the group whose caption opens by naming a framework,
architecture, pipeline or workflow, else an overview, else the first one. All
of its panels are downloaded before any is written, so a missing panel never
leaves a partial group carrying the whole group's caption.
"""

import hashlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from isbe.core import http, paths

from .arxiv_html import ARXIV_HTML, Figure, image_url
from .calls import EnrichError, call, header, read, status_line, step
from .text import WHITESPACE

# A bigger figure is left out: reports and emails carry it inline.
MAX_FIGURE = 500 << 10
MAX_HTML_PANEL = 2 << 20
MAX_HTML_FIGURE = 5 << 20

_METHOD = ("framework", "architecture", "pipeline", "workflow", "schematic", "架构", "框架", "流程")
_OVERVIEW = ("overview", "illustration", "总体")

type Download = Callable[[str], tuple[str, bytes]]


def figure_rank(caption: str) -> int:
    # The caption's opening names the figure; later words describe results.
    head = caption[:80].lower()
    if any(word in head for word in _METHOD):
        return 0
    if any(word in head for word in _OVERVIEW):
        return 1
    return 2


def image_extension(content_type: str, data: bytes) -> str:
    """The file extension, when the declared type and the bytes agree."""
    mime = content_type.split(";")[0].strip(WHITESPACE)
    if mime == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if mime == "image/jpeg" and data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if mime == "image/webp" and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    raise EnrichError("figure is not a supported PNG, JPEG or WebP image")


def download_html_image(client: http.Http, url: str) -> tuple[str, bytes]:
    """One panel; the URL must still pass the page's image check, and redirects
    are not followed, so the request cannot leave arXiv's HTML tree."""
    checked = image_url(ARXIV_HTML, url)
    if checked is None:
        raise EnrichError("unsupported figure URL")
    with call(client, "GET", checked, follow_redirects=False) as response:
        if response.status_code != 200:
            raise EnrichError(f"figure download returned HTTP {status_line(response.status_code)}")
        content_type = header(response, "content-type") or ""
        data = read(response, MAX_HTML_PANEL)
    return image_extension(content_type, data), data


def save_html_figure(data: Path, figures: Sequence[Figure], download: Download) -> dict[str, Any]:
    """`{"files", "caption"}` for the chosen group, `{}` when no group qualifies."""
    usable = [f for f in figures if f.images and f.caption.strip(WHITESPACE)]
    if not usable:
        return {}
    figure = min(usable, key=lambda f: figure_rank(f.caption))
    panels: list[tuple[str, bytes]] = []
    total = 0
    for url in figure.images:
        ext, image = download(url)
        total += len(image)
        if total > MAX_HTML_FIGURE:
            raise EnrichError("figure group exceeds 5 MiB")
        panels.append((f"figures/{hashlib.sha256(image).hexdigest()}.{ext}", image))
    folder = data / "figures"
    paths.check_path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for file, image in panels:
        with step("save figure"):
            write(data / file, image)
        files.append(file)
    return {"files": files, "caption": figure.caption}


def write(path: Path, data: bytes) -> None:
    """Replace a figure file; mode 0666 less the umask."""
    paths.atomic_write(path, data, mode=0o666)

