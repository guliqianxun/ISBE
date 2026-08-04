"""HTML email renderer for ISBE notify module.

Converts a markdown artifact to a branded HTML email body using
the "Academic Ink" visual system defined in ft-002.

Contract:
  render_html() raises on any failure — the caller (send_digest_notification)
  catches and falls back to plaintext. This module never swallows exceptions.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import markdown
import nh3
import premailer
from jinja2 import Environment, FileSystemLoader

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_NAME = "email.html.j2"

_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=False,  # HTML content injected intentionally
)

# The artifact markdown embeds LLM output, which in turn embeds crawled/RSS
# text — untrusted. nh3 defaults already allow the tags the templates rely on
# (tables, details/summary, img, sup for footnotes); we extend attributes with
# class/id (footnote anchors, styling hooks) and allow data: URLs because
# scripts/render_report.py inlines figures as base64 data URIs.
_ALLOWED_ATTRIBUTES = {
    tag: set(attrs) for tag, attrs in nh3.ALLOWED_ATTRIBUTES.items()
}
_ALLOWED_ATTRIBUTES.setdefault("*", set()).update({"class", "id"})
_ALLOWED_URL_SCHEMES = {"http", "https", "mailto", "data"}


def _sanitize(body_html: str) -> str:
    return nh3.clean(
        body_html,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes=_ALLOWED_URL_SCHEMES,
    )


# Some mail clients clip large HTML (Gmail ~102KB) — but content display wins
# by default: generous budget, tunable via ISBE_EMAIL_IMAGE_BUDGET for
# clip-prone recipients. Figures over budget become a note; the artifact file
# keeps every figure regardless.
_EMAIL_IMAGE_BUDGET_DEFAULT = 800_000
_DATAURI_IMG_RE = re.compile(r"!\[[^\]]*\]\((data:image/[^)]+)\)")


def _email_image_budget() -> int:
    try:
        return int(os.getenv("ISBE_EMAIL_IMAGE_BUDGET", str(_EMAIL_IMAGE_BUDGET_DEFAULT)))
    except ValueError:
        return _EMAIL_IMAGE_BUDGET_DEFAULT


def _cap_inline_images(artifact_md: str, budget: int | None = None) -> str:
    if budget is None:
        budget = _email_image_budget()
    spent = 0

    def _repl(m: re.Match) -> str:
        nonlocal spent
        uri = m.group(1)
        if spent + len(uri) <= budget:
            spent += len(uri)
            return m.group(0)
        return "*（图片过大，邮件中省略——见 artifact 原文）*"

    return _DATAURI_IMG_RE.sub(_repl, artifact_md)


def _inline_css(html: str) -> str:
    # allow_network=False: untrusted HTML must never cause premailer to fetch
    # <link rel=stylesheet> URLs from inside the worker's network (SSRF).
    return premailer.transform(html, allow_network=False)


def render_html(
    *,
    topic_label: str,
    period_label: str,
    artifact_md: str,
    artifact_path: object,  # Path | None — not used for rendering, accepted for API compat
) -> str:
    """Render *artifact_md* (Markdown text) to a fully inlined HTML email string.

    Parameters
    ----------
    topic_label:
        Topic identifier, e.g. ``"nowcasting"``.  Uppercased in the banner.
    period_label:
        Period string, e.g. ``"2026-W21"``.
    artifact_md:
        Markdown source text to render as the email body.
    artifact_path:
        Accepted for API symmetry; not used inside this function.

    Returns
    -------
    str
        Complete HTML document with all ``<style>`` rules inlined via
        ``premailer.transform``, ready to pass to
        ``EmailMessage.add_alternative(..., subtype="html")``.

    Raises
    ------
    Any exception from ``markdown``, ``jinja2``, or ``premailer`` propagates
    to the caller — no swallowing.
    """
    body_html = _sanitize(
        markdown.markdown(
            _cap_inline_images(artifact_md),
            extensions=["tables", "fenced_code", "footnotes", "md_in_html"],
        )
    )

    template = _jinja_env.get_template(_TEMPLATE_NAME)
    rendered = template.render(
        topic_label_upper=topic_label.upper(),
        period_label=period_label,
        body_html=body_html,
    )

    return _inline_css(rendered)
