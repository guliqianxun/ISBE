"""The email for a report (or the test email): its text, HTML with inline
figures, and the size limits.

The message is built once as the ZeptoMail API body; the SMTP transport turns
the same body into MIME, so both transports send identical content.
"""

import base64
import sqlite3
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from isbe.core import jsonfmt
from isbe.core.markdown import render_markdown
from isbe.core.validators import byte_len, is_control, trim
from isbe.reports import library

from .config import Config, MailError

MAX_IMAGE = 2 << 20
MAX_IMAGES = 5 << 20
MAX_MESSAGE = 10 << 20
TEST_SUBJECT = "ISBE · 邮件配置测试 / Email test"
# Email clients do not load the reader CSS; block images keep captions underneath.
_IMAGE_STYLE = '<img style="display:block;max-width:100%;height:auto;margin:14px auto 4px" '
_FIGURE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}

_TEST_MD = (
    "# ISBE 邮件测试 / Email test\n\n中文正文与 [ISBE](https://github.com/) 链接。\n\n"
    "| 项目 | 状态 |\n| --- | --- |\n| 邮件配置 | 测试 |\n"
)
_TEST_IMAGE = (
    "<p>内联图片 / Inline image: <img src=\"cid:isbe-test\" alt=\"测试配图\" width=\"32\" "
    'height="32" style="border:1px solid #777"></p>'
)
_TEST_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAIAAABvFaqvAAAAH0lEQVR4nGOQnN1OFcQwatCoQaMGjRo0atCoQQNvEADeYMTfnGqJNwAAAABJRU5ErkJggg=="
)


def subject(topic: str, period: str) -> str:
    return f"ISBE · {topic} · {period}"


def report_content(
    conn: sqlite3.Connection, data: Path, topic: str, period: str
) -> tuple[str, str, list[dict[str, Any]]]:
    """The exact report's markdown, its email HTML and the inline figures."""
    if any(
        not trim(s) or byte_len(s) > 200 or any(map(is_control, s)) for s in (topic, period)
    ):
        raise MailError("The exact report topic and period are required")
    try:
        found = library.find_report(conn, data, topic, period)
    except (library.ReportMissing, OSError, UnicodeDecodeError, sqlite3.Error):
        raise MailError(
            "The report file is missing, unreadable or outside the reports directory"
        ) from None
    if found is None:
        raise MailError("The exact report was not found or its path is unsafe")
    md = found[1]
    if byte_len(md) > MAX_MESSAGE // 2:
        raise MailError("Report is too large for email")
    html, images = inline_images(data, render_markdown(md))
    return md, html.replace("<img ", _IMAGE_STYLE), images


def test_content() -> tuple[str, str, list[dict[str, Any]]]:
    """The test email: Chinese text, a link, a table and one inline image."""
    image = {"cid": "isbe-test", "name": "test.png", "mime_type": "image/png", "content": _TEST_PNG}
    return _TEST_MD, render_markdown(_TEST_MD) + _TEST_IMAGE, [image]


class _Sources(HTMLParser):
    """Every `<img>` start tag's `src` (decoded, "" when absent), in order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            self.sources.append(next((v or "" for k, v in attrs if k == "src"), ""))


def _figure(data: Path, name: str) -> bytes:
    unreadable = MailError("Report figure is missing or unreadable")
    try:
        root = data.resolve(strict=True)
        figures = (data / "figures").resolve(strict=True)
        file = (data / "figures" / name).resolve(strict=True)
    except OSError:
        raise unreadable from None
    if not figures.is_relative_to(root) or not file.is_relative_to(figures):
        raise MailError("Report figure is outside the figures directory")
    try:
        with open(file, "rb") as handle:
            return handle.read(MAX_IMAGE + 1)
    except OSError:
        raise unreadable from None


def _matches(mime: str, content: bytes) -> bool:
    if mime == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    return content.startswith(b"RIFF") and content[8:12] == b"WEBP"


def inline_images(data: Path, html: str) -> tuple[str, list[dict[str, Any]]]:
    """Replace each local figure with a `cid:` reference and attach it.

    Every image must be a PNG, JPEG or WebP file directly under
    `<data>/figures/` (also after following links), at most 2 MiB each and
    5 MiB together. `render_markdown` already drops every other image; the
    checks stay so no HTML can make an email fetch or attach
    anything else.
    """
    parser = _Sources()
    parser.feed(html)
    parser.close()
    seen: set[str] = set()
    images: list[dict[str, Any]] = []
    total = 0
    for src in parser.sources:
        if src in seen:
            continue
        seen.add(src)
        if src.startswith("/figures/"):
            name = src.removeprefix("/figures/")
        elif src.startswith("../../figures/"):
            name = src.removeprefix("../../figures/")
        else:
            raise MailError("Report contains an image that is not a local report figure")
        if not name or name.startswith(".") or not set(name) <= _FIGURE_CHARS:
            raise MailError("Report contains an unsafe figure reference")
        mime = _MIME.get(name.rsplit(".", 1)[-1])
        if mime is None:
            raise MailError("Report figure format is unsupported; use PNG, JPEG or WebP")
        content = _figure(data, name)
        if len(content) > MAX_IMAGE:
            raise MailError("A report figure exceeds 2 MiB; no email was sent")
        total += len(content)
        if total > MAX_IMAGES:
            raise MailError("Report figures exceed 5 MiB in total; no email was sent")
        if not _matches(mime, content):
            raise MailError("Report figure content does not match its image format")
        cid = f"isbe-{len(images)}"
        # Sanitized local sources contain only ASCII filename characters, so this exact
        # attribute is unambiguous.
        html = html.replace(f'src="{src}"', f'src="cid:{cid}"')
        encoded = base64.b64encode(content).decode("ascii")
        images.append({"cid": cid, "name": name, "mime_type": mime, "content": encoded})
    return html, images


def payload(
    config: Config,
    to: list[str],
    subject: str,
    text: str,
    html: str,
    images: list[dict[str, Any]],
) -> dict[str, Any]:
    """The ZeptoMail request body, which is also what the SMTP transport sends."""
    body = {
        "from": {"address": config.sender, "name": config.name},
        "to": [{"email_address": {"address": address}} for address in to],
        "subject": subject,
        "textbody": text,
        "htmlbody": html,
        "inline_images": images,
        "track_clicks": False,
        "track_opens": False,
    }
    if byte_len(jsonfmt.dumps(body)) > MAX_MESSAGE:
        raise MailError("Email exceeds the 10 MiB message limit")
    return body
