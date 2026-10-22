"""`/help`: the README and the documents shipped with it, one page each.

`?doc=` picks a page from a fixed list; anything else is not found, so no
other file can be read through it. Links between these documents stay
inside `/help`, with their `#heading` anchors; other repository paths
(examples, the licence) are not served, so their links are dropped and the
text remains. Web links are kept.
"""

import posixpath
from collections.abc import Callable
from functools import cache
from html import escape
from urllib.parse import urlsplit

from isbe.core import assets
from isbe.core.markdown import render_markdown

# `?doc=` name → (path from the repository root, navigation label), in navigation order.
DOCS: dict[str, tuple[str, str]] = {
    "readme": ("README.md", "README"),
    "readme-en": ("README.en.md", "README (English)"),
    "configuration": ("docs/configuration.md", "配置参考"),
    "source-interface": ("docs/source-interface.md", "来源接口"),
    "searxng": ("deploy/searxng/README.md", "SearXNG"),
    "design": ("docs/DESIGN.md", "Design"),
    "architecture": ("docs/architecture.md", "Architecture"),
    "upgrading-from-rust": ("docs/upgrading-from-rust.md", "Upgrading from Rust"),
    "docs": ("docs/README.md", "Docs index"),
}
_BY_PATH = {path: name for name, (path, _) in DOCS.items()}

_STYLE = """
body { margin: 0; font: 15px/1.7 system-ui, sans-serif; color: #1f2328; background: #fff; }
nav { position: sticky; top: 0; display: flex; flex-wrap: wrap; gap: 4px 16px; padding: 10px 24px;
  border-bottom: 1px solid #d0d7de; background: #f6f8fa; font-size: 14px; }
nav a { color: #0969da; text-decoration: none; }
nav a[aria-current] { color: inherit; font-weight: 600; }
main { max-width: 920px; margin: 0 auto; padding: 8px 24px 64px; }
a { color: #0969da; }
code, pre { font: 13px/1.5 ui-monospace, monospace; background: #f6f8fa; border-radius: 4px; }
code { padding: 1px 4px; }
pre { padding: 12px 16px; overflow-x: auto; }
pre code { padding: 0; }
table { border-collapse: collapse; display: block; overflow-x: auto; }
th, td { border: 1px solid #d0d7de; padding: 4px 10px; vertical-align: top; }
blockquote { margin: 0; padding-left: 16px; border-left: 3px solid #d0d7de; color: #59636e; }
h1, h2 { border-bottom: 1px solid #d0d7de; padding-bottom: 4px; }
h1, h2, h3 { scroll-margin-top: 56px; }
@media (prefers-color-scheme: dark) {
  body { color: #e6edf3; background: #0d1117; }
  nav { background: #161b22; border-color: #30363d; }
  nav a, a { color: #4493f8; }
  code, pre { background: #161b22; }
  th, td, h1, h2 { border-color: #30363d; }
  blockquote { border-color: #30363d; color: #9198a1; }
}
"""


def href(name: str, fragment: str = "") -> str:
    base = "/help" if name == "readme" else f"/help?doc={name}"
    return f"{base}#{fragment}" if fragment else base


def _links(path: str) -> Callable[[str], str | None]:
    """Link targets as written in `path`, resolved against its directory."""

    def link(target: str) -> str | None:
        url = urlsplit(target)
        if url.scheme or url.netloc or not url.path:
            return target
        name = _BY_PATH.get(posixpath.normpath(posixpath.join(posixpath.dirname(path), url.path)))
        return None if name is None else href(name, url.fragment)

    return link


@cache
def page(name: str) -> bytes | None:
    """The help page for `?doc=name`, or None for a name not in `DOCS`."""
    if name not in DOCS:
        return None
    path, label = DOCS[name]
    body = render_markdown(assets.text(*path.split("/")), _links(path))
    current = ' aria-current="page"'
    nav = "".join(
        f'<a href="{href(other)}"{current if other == name else ""}>{escape(text)}</a>'
        for other, (_, text) in DOCS.items()
    )
    return (
        '<!doctype html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>ISBE · {escape(label)}</title>\n<style>{_STYLE}</style>\n</head>\n<body>\n"
        f'<nav aria-label="Help"><a href="/">← ISBE</a>{nav}</nav>\n<main>\n{body}</main>\n</body>\n</html>\n'
    ).encode()
