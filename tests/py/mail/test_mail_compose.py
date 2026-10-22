"""Report email content, inline figures and size limits."""

import base64
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from isbe import mail
from isbe.core import db
from isbe.core.markdown import render_markdown
from isbe.mail import compose
from isbe.mail.compose import (
    MAX_IMAGE,
    MAX_MESSAGE,
    inline_images,
    payload,
    report_content,
)

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ1cAAAAASUVORK5CYII="
)
HISTORY = (
    "# 历史报告\n\n[论文](https://example.org/paper)\n\n| 方法 | 分数 |\n| --- | --- |\n| 基线 | 8 |\n\n"
    "![配图](../../figures/owned.png)\n<sub>Figure 1: 图片下方的图题。</sub>\n\nH<sub>2</sub>O\n\n"
    '`<img src="/figures/not-real.png">`\n'
)


@pytest.fixture
def workspace(data: Path) -> Iterator[tuple[sqlite3.Connection, Path]]:
    (data / "reports" / "topic").mkdir()
    (data / "figures").mkdir()
    conn = db.connect(data / "isbe.db")
    yield conn, data
    conn.close()


def add_report(conn: sqlite3.Connection, data: Path, period: str, md: str, topic: str = "topic") -> None:
    path = f"reports/{topic}/{period}.md"
    (data / path).parent.mkdir(parents=True, exist_ok=True)
    (data / path).write_text(md, encoding="utf-8")
    conn.execute(
        "INSERT INTO reports (id,topic_id,period,kind,path,fingerprint,created_at) "
        "VALUES (?1,?2,?3,'weekly',?4,'{}',?3)",
        (f"{topic}/{period}", topic, period, path),
    )


def config() -> mail.Config:
    return mail.load(
        {
            "ZEPTOMAIL_API_URL": "https://api.zeptomail.com/v1.1/email",
            "ZEPTOMAIL_TOKEN": "dummy-secret",
            "SMTP_FROM": "sender@example.com",
            "SMTP_FROM_NAME": "研究报告",
        }.get
    )


def error(call: object, *args: object) -> str:
    with pytest.raises(mail.MailError) as caught:
        call(*args)  # type: ignore[operator]
    assert caught.value.status == 422
    return caught.value.message


def test_exact_report_content_and_cid_images_preserve_chinese_tables_and_links(
    workspace: tuple[sqlite3.Connection, Path],
) -> None:
    conn, data = workspace
    add_report(conn, data, "2026-W40", HISTORY)
    add_report(conn, data, "2026-W41", "# 最新报告，不应发送")
    (data / "figures" / "owned.png").write_bytes(PNG)
    text, html, images = report_content(conn, data, "topic", "2026-W40")
    assert "历史报告" in text and "最新报告" not in text
    assert "<table>" in html and 'href="https://example.org/paper"' in html
    assert 'src="cid:isbe-0"' in html and "配图" in html
    assert "display:block;max-width:100%;height:auto" in html
    assert "<sub>Figure 1: 图片下方的图题。</sub>" in html
    assert "H<sub>2</sub>O" in html
    assert html.count("style=") == 1
    assert "style=" not in render_markdown(text)
    assert len(images) == 1 and images[0]["name"] == "owned.png"
    assert images[0] == {"cid": "isbe-0", "name": "owned.png", "mime_type": "image/png",
                         "content": base64.b64encode(PNG).decode()}
    assert 'src="cid:isbe-0"' in inline_images(data, '<img src="../../figures/owned.png">')[0]
    body = payload(config(), ["recipient@example.com"], compose.subject("topic", "2026-W40"), text, html, images)
    assert body["subject"] == "ISBE · topic · 2026-W40"
    assert body["from"] == {"address": "sender@example.com", "name": "研究报告"}
    assert body["to"] == [{"email_address": {"address": "recipient@example.com"}}]
    assert body["track_clicks"] is False and body["track_opens"] is False
    for topic, period in [("", "2026-W40"), ("topic", ""), ("missing", "2026-W40"), ("topic", "missing"),
                          (" ", "2026-W40"), ("topic", "x" * 201), ("topic\n", "2026-W40")]:
        error(report_content, conn, data, topic, period)
    (data / "reports/topic/2026-W40.md").unlink()
    assert "missing" in error(report_content, conn, data, "topic", "2026-W40")
    (data / "outside-report.md").write_text("must not send this file")
    (data / "reports/topic/2026-W40.md").symlink_to(data / "outside-report.md")
    assert "unsafe" in error(report_content, conn, data, "topic", "2026-W40")


def test_figures_reject_missing_external_unsafe_large_and_symlink_targets(data: Path) -> None:
    (data / "figures").mkdir()

    def html(src: str) -> str:
        return render_markdown(f"![图]({src})")

    assert "missing" in error(inline_images, data, html("/figures/missing.png"))
    # render_markdown already removes images that are not local figures, so these
    # never reach the email; inline_images still refuses them in raw HTML (defense in depth).
    for src in ["/figures/../secret.png", "/figures/%2e%2e.png", "https://example.com/figure.png"]:
        assert "<img" not in html(src), src
        assert inline_images(data, html(src)) == (html(src), [])
    assert "not a local report figure" in error(inline_images, data, '<img src="https://example.com/figure.png">')
    assert "not a local report figure" in error(inline_images, data, "<img alt=x>")
    assert "not a local report figure" in error(inline_images, data, '<IMG SRC="data:image/png;base64,AA">')
    for src in ["/figures/../secret.png", "/figures/%2e%2e.png", "/figures/.hidden.png", "/figures/a/b.png",
                "/figures/"]:
        assert "unsafe" in error(inline_images, data, f'<img src="{src}">'), src
    assert "unsupported" in error(inline_images, data, '<img src="/figures/a.gif">')
    assert "unsupported" in error(inline_images, data, '<img src="/figures/a.PNG">')
    (data / "figures" / "large.png").write_bytes(b"\0" * (MAX_IMAGE + 1))
    assert "2 MiB" in error(inline_images, data, html("/figures/large.png"))
    (data / "figures" / "invalid.png").write_bytes(b"not an image")
    assert "format" in error(inline_images, data, html("/figures/invalid.png"))
    (data / "outside.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (data / "figures" / "link.png").symlink_to(data / "outside.png")
    assert "outside" in error(inline_images, data, html("/figures/link.png"))
    image = b"\x89PNG\r\n\x1a\n" + b"\0" * (MAX_IMAGE - 8)
    for name in ["one", "two", "three"]:
        (data / "figures" / f"{name}.png").write_bytes(image)
    assert len(inline_images(data, html("/figures/one.png") + html("/figures/one.png"))[1]) == 1
    group = "".join(html(f"/figures/{name}.png") for name in ["one", "two", "three"])
    assert "5 MiB" in error(inline_images, data, group)
    assert "10 MiB" in error(payload, config(), ["a@example.com"], "title", "x" * MAX_MESSAGE, "", [])


def test_jpeg_and_webp_figures_are_recognized(data: Path) -> None:
    (data / "figures").mkdir()
    (data / "figures" / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0rest")
    (data / "figures" / "b.webp").write_bytes(b"RIFF\0\0\0\0WEBPVP8 ")
    html, images = inline_images(data, '<img src="/figures/a.jpg"><img src="/figures/b.webp">')
    assert [i["mime_type"] for i in images] == ["image/jpeg", "image/webp"]
    assert html == '<img src="cid:isbe-0"><img src="cid:isbe-1">'


def test_test_email_content_has_its_inline_image() -> None:
    text, html, images = compose.test_content()
    assert text.startswith("# ISBE 邮件测试 / Email test")
    assert "<table>" in html and 'src="cid:isbe-test"' in html
    assert [i["cid"] for i in images] == ["isbe-test"]
    assert base64.b64decode(images[0]["content"]).startswith(b"\x89PNG")
    assert compose.TEST_SUBJECT == "ISBE · 邮件配置测试 / Email test"
