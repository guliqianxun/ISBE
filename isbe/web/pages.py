"""Responses that are not JSON: report figures, the research evidence page,
and the link rewriting that keeps an administrator inside the workspace they
review.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .wire import Reply, form_pairs, form_value, not_found, reply

_FIGURE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
_NAME = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def figure(data: Path, name: str) -> Reply:
    """A file from `figures/`; the name may not leave that directory, also through links."""
    if not name or name.startswith(".") or not all(c in _NAME for c in name):
        return not_found()
    content_type = _FIGURE_TYPES.get(name.rsplit(".", 1)[-1])
    if content_type is None:
        return not_found()
    try:
        root = data.resolve(strict=True)
        figures = (data / "figures").resolve(strict=True)
        path = (figures / name).resolve(strict=True)
    except (OSError, RuntimeError):
        return not_found()
    if not _inside(figures, root) or not _inside(path, figures):
        return not_found()
    try:
        body = path.read_bytes()
    except OSError:
        return not_found()
    return reply(200, content_type, body).with_header("Cache-Control", "private, no-store")


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _number(value: Any) -> str:
    """`Value`'s Display for the offsets: a number, or `null` when absent."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value) if isinstance(value, int) else "null"


def research_evidence_page(e: Mapping[str, Any], query: Mapping[str, str]) -> str:
    def text(key: str) -> str:
        value = e.get(key)
        return value if isinstance(value, str) else ""

    def field(key: str) -> str:
        return _escape(text(key))

    back = _escape(form_pairs([(k, query[k]) for k in ("topic", "period", "owner") if k in query]))
    original = f"{back}&amp;id={field('source_id')}"
    limitations = e.get("limitations")
    limits = (
        "；".join(_escape(v) for v in limitations if isinstance(v, str))
        if isinstance(limitations, list)
        else ""
    )
    publisher = _escape(text("publisher") or "发布机构未注明")
    published = e.get("published_on")
    published = _escape(published if isinstance(published, str) else "发布日期未确认")
    title = field("title")
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" '
        f'content="width=device-width,initial-scale=1"><title>来源 · {title}</title><style>'
        "body{max-width:850px;margin:40px auto;padding:0 24px;font:17px/1.8 system-ui;"
        "color:#20302b;background:#fbfaf6}a{color:#176455}blockquote{margin:20px 0;"
        "padding:16px 24px;border-left:4px solid #176455;background:#edf2ec;white-space:pre-wrap}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.8 system-ui}small{"
        "overflow-wrap:anywhere;color:#58645e}summary{cursor:pointer}dt{font-weight:600}"
        f'dd{{margin:0 0 12px}}</style><a href="/reports?{back}">← 返回报告</a><h1>{title}</h1>'
        f"<p>{publisher} · {published}</p><blockquote>{field('quote')}</blockquote><dl><dt>适用范围"
        f'</dt><dd>{field("coverage")}</dd></dl><p><a href="{field("url")}" rel="noreferrer">'
        f"{publisher}｜{title}</a></p><details><summary>核验记录</summary><p>{field('location')}"
        f"</p><dl><dt>阅读深度</dt><dd>{field('reading_depth')}</dd><dt>使用限制</dt><dd>{limits}"
        f'</dd></dl><p><a href="/api/research/original?{original}">下载本期冻结原件</a></p>'
        f"<small>原件 SHA256：{field('sha256')}<br>文本 SHA256：{field('text_sha256')}<br>"
        f"文本位置：UTF-8 字节 {_number(e.get('start'))}–{_number(e.get('end'))}（左闭右开）</small>"
        f"<details><summary>查看本期保存的材料文本</summary><pre>{field('text')}</pre></details>"
        "</details></html>"
    )


def _rewrite(html: str, marker: str, suffix: str) -> str:
    """Append `suffix` to every attribute value that starts with `marker`."""
    out: list[str] = []
    rest = html
    while (i := rest.find(marker)) >= 0:
        out.append(rest[: i + len(marker)])
        after = rest[i + len(marker) :]
        j = after.find('"')
        if j < 0:
            rest = after
            break
        out.append(after[:j] + suffix + '"')
        rest = after[j + 1 :]
    out.append(rest)
    return "".join(out)


def figure_owner_links(html: str, owner: str) -> str:
    """Figures of another workspace load with its `?owner=`."""
    return _rewrite(html, 'src="/figures/', f"?owner={owner}")


def research_owner_links(html: str, owner: str) -> str:
    """Evidence links of another workspace keep its owner."""
    return _rewrite(html, 'href="/api/research/evidence?', f"&amp;owner={form_value(owner)}")
