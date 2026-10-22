"""Which materials are the same article.

`identity(url)` gives the key materials are grouped by, in this order:

- `doi`: a DOI the address carries (doi.org links, a `/doi/` or Springer-style
  `/article/` path, a `doi=` parameter), lowercased as DOIs are case-insensitive;
- `site`: a site's own article ID, for the sites in `_SITE_IDS` only;
- `url`: the address without what never changes the article served: the
  scheme (http and https), host case, a leading `www.`, the default port, the
  fragment, a trailing slash, parameter order and the tracking parameters in
  `TRACKING`.

Titles are never compared, so two articles with one title stay apart, and a
reprint at another address is neither merged with nor declared independent of
its original. A DOI the path only partly matches gives a different key, never a
wrong one: a missed grouping keeps both records visible, a wrong one would hide
an article. Nothing here is stored; listings group materials when they read them.
"""

import re
from functools import lru_cache
from typing import NamedTuple
from urllib.parse import parse_qsl, unquote, urlsplit

# Parameters that record how a reader arrived, not which article is served.
# Kept short on purpose: an unknown parameter stays part of the key.
TRACKING = frozenset(
    {
        "spm",
        "from",
        "isappinstalled",
        "fbclid",
        "gclid",
        "yclid",
        "dclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "share_token",
    }
)
_DOI = re.compile(r"10\.\d{4,9}/\S+")
_DOI_PREFIX = re.compile(r"10\.\d{4,9}")
# Path segments after which publishers place the DOI (`/doi/abs/10.1002/x`, `/article/10.1007/x`).
_DOI_MARKERS = frozenset({"doi", "abs", "abstract", "full", "pdf", "epdf", "article", "chapter"})
_JIEMIAN = re.compile(r"/article/(\d+)\.html")


class Identity(NamedTuple):
    kind: str
    key: str

    def to_json(self) -> dict[str, str]:
        return {"kind": self.kind, "key": self.key}


def _wechat(host: str, path: str, query: list[tuple[str, str]]) -> str | None:
    # An article link names its account, message and position; `sn`, `chksm` and
    # `scene` vary with how it was shared.
    if host != "mp.weixin.qq.com" or path != "/s":
        return None
    fields = dict(query)
    if not all(fields.get(name) for name in ("__biz", "mid", "idx")):
        return None
    return f"mp.weixin.qq.com:{fields['__biz']}:{fields['mid']}:{fields['idx']}"


def _jiemian(host: str, path: str, query: list[tuple[str, str]]) -> str | None:
    # The desktop and mobile sites serve one article under the same number
    # (articles/date_headers.py reads both layouts of `/article/<n>.html`).
    if host not in ("jiemian.com", "m.jiemian.com"):
        return None
    match = _JIEMIAN.fullmatch(path)
    return None if match is None else f"jiemian.com:article:{match[1]}"


_SITE_IDS = (_wechat, _jiemian)


def _doi(host: str, path: str, query: list[tuple[str, str]]) -> str | None:
    if host in ("doi.org", "dx.doi.org"):
        found = _DOI.fullmatch(unquote(path.lstrip("/")))
        return None if found is None else found[0].lower()
    for name, value in query:
        if name.lower() == "doi" and _DOI.fullmatch(value):
            return value.lower()
    segments = unquote(path).split("/")
    for index, segment in enumerate(segments[1:-1], start=1):
        if _DOI_PREFIX.fullmatch(segment) and segments[index - 1].lower() in _DOI_MARKERS:
            found = _DOI.fullmatch("/".join(segments[index:]))
            if found is not None:
                return found[0].lower()
    return None


def _tracking(name: str) -> bool:
    name = name.lower()
    return name in TRACKING or name.startswith("utm_")


@lru_cache(maxsize=65536)
def identity(url: str) -> Identity:
    """The key a material at `url` is grouped by; never raises."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return Identity("url", url.split("#", 1)[0])
    if not host:
        return Identity("url", url.split("#", 1)[0])
    host = host.removeprefix("www.")
    pairs = [p for p in parts.query.split("&") if p]
    query = parse_qsl(parts.query, keep_blank_values=True)
    if (doi := _doi(host, parts.path, query)) is not None:
        return Identity("doi", doi)
    for rule in _SITE_IDS:
        if (key := rule(host, parts.path, query)) is not None:
            return Identity("site", key)
    scheme = parts.scheme.lower()
    if port is not None and not (port == 80 and scheme == "http") and not (port == 443 and scheme == "https"):
        host = f"{host}:{port}"
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    kept = sorted(p for p in pairs if not _tracking(unquote(p.split("=", 1)[0].replace("+", " "))))
    return Identity("url", host + path + ("?" + "&".join(kept) if kept else ""))
