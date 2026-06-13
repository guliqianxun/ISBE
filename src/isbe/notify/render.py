"""HTML email renderer for ISBE notify module.

Converts a markdown artifact to a branded HTML email body using
the "Academic Ink" visual system defined in ft-002.

Contract:
  render_html() raises on any failure — the caller (send_digest_notification)
  catches and falls back to plaintext. This module never swallows exceptions.
"""
from __future__ import annotations

from pathlib import Path

import markdown
import premailer
from jinja2 import Environment, FileSystemLoader

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_NAME = "email.html.j2"

_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=False,  # HTML content injected intentionally
)


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
    body_html = markdown.markdown(
        artifact_md,
        extensions=["tables", "fenced_code", "footnotes"],
    )

    template = _jinja_env.get_template(_TEMPLATE_NAME)
    rendered = template.render(
        topic_label_upper=topic_label.upper(),
        period_label=period_label,
        body_html=body_html,
    )

    inlined = premailer.transform(rendered)
    return inlined
