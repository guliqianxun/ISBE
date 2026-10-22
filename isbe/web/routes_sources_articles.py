"""POST /api/sources/article: the original text of one article of a media report.

A briefing answers with the original its editor used; a plain media week
reads the article cache, fetching the page once if needed. Failures are
told apart by what the reader can do, never by the upstream's details.
"""

import logging
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from isbe import articles
from isbe.core import settings
from isbe.core.errors import HttpError
from isbe.core.validators import trim
from isbe.reports import media
from isbe.sources import registry

from .routes_sources_body import ARTICLE, body
from .routes_sources_reply import failure, task_settings
from .wire import Reply, Request, error, json_reply

log = logging.getLogger(__name__)

_UNSUPPORTED = (422, "unsupported_body", "这个网站的正文暂未能完整读取，请点击报道标题查看原文。")
_UNAVAILABLE = (502, "source_unavailable", "暂时无法连接报道来源，请稍后重试，或点击报道标题查看原文。")
_UNREADABLE = (500, "article_unavailable", "已收录的原文暂时无法读取，请稍后重试。")
_MATERIALS = "这期周报的材料暂时无法读取，请打开来源链接查看。"


def expand(conn: Connection, data: Path, req: Request) -> Reply:
    input = body(req, ARTICLE)
    topic, period, id = input["topic"], input["period"], input["id"]
    if not trim(id) or not trim(topic) or not trim(period):
        return error(400, "请指定周报、期次和报道。")
    try:
        value = _original(conn, data, topic, period, id)
    except Exception as e:
        return article_failure(e)
    return json_reply(200, value)


def _original(conn: Connection, data: Path, topic: str, period: str, id: str) -> dict[str, Any]:
    try:
        listed = media.articles(conn, data, topic, period)
    except Exception as e:
        if registry.status(e) == 400:
            raise
        raise HttpError(409, _MATERIALS) from None
    article = next((item for item in listed if item.get("id") == id), None)
    if article is None:
        raise HttpError(404, "这期周报中没有找到这篇报道。")
    frozen = media.original(conn, data, topic, period, id)
    if frozen is not None:
        return frozen
    url = article.get("url")
    with settings.task_scope(task_settings(data)):
        return articles.get_or_fetch(conn, url if isinstance(url, str) else "")


def article_failure(e: Exception) -> Reply:
    """The reply for a failed article read: what the reader can do, without the upstream's details."""
    if articles.caused_by(e, articles.UnsupportedBody) is not None:
        status, code, message = _UNSUPPORTED
    elif articles.caused_by(e, articles.FetchFailed) is not None:
        status, code, message = _UNAVAILABLE
    elif registry.status(e) == 500:
        status, code, message = _UNREADABLE
    else:
        return failure(e)
    # Error chains from HTTP clients can contain URLs or credentials.
    log.warning("article expansion failed: %s (HTTP %s)", code, status)
    return json_reply(status, {"error": message, "code": code})
