"""The article route's failure mapping and an
original read from a local site."""

import json
import logging
import sqlite3
from pathlib import Path

import pytest
from web_sources_helpers import request, weekly_fixture

from isbe import articles
from isbe.articles import FetchFailed, UnsupportedBody
from isbe.core.errors import HttpError
from isbe.web.console import Console
from isbe.web.routes_sources_articles import article_failure


def _chained(cause: Exception, outer: Exception) -> Exception:
    try:
        raise outer from cause
    except Exception as e:
        return e


def test_original_read_errors_explain_retryability_without_exposing_remote_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cases = [
        # The cache wraps every read failure, an unsupported page included.
        (_chained(UnsupportedBody(), FetchFailed()), 422, "unsupported_body"),
        (_chained(ValueError("https://user:secret@example.org/private"), FetchFailed()), 502, "source_unavailable"),
        (ValueError("internal cache path"), 500, "article_unavailable"),
    ]
    caplog.set_level(logging.WARNING)
    for error, status, code in cases:
        reply = article_failure(error)
        assert reply.status == status
        value = json.loads(reply.body)
        assert value["code"] == code
        text = reply.body.decode()
        assert "secret" not in text and "example.org" not in text and "internal cache path" not in text
    assert "secret" not in caplog.text and "internal cache path" not in caplog.text
    assert "article expansion failed: source_unavailable (HTTP 502)" in caplog.text
    exact = json.loads(article_failure(UnsupportedBody()).body)
    assert exact == {"error": "这个网站的正文暂未能完整读取，请点击报道标题查看原文。", "code": "unsupported_body"}
    # Errors the user can act on keep their own status and message.
    plain = article_failure(HttpError(404, "这期周报中没有找到这篇报道。"))
    assert (plain.status, json.loads(plain.body)) == (404, {"error": "这期周报中没有找到这篇报道。"})


def test_article_inputs_are_required_and_identifiers_checked(app: Console) -> None:
    for body in [{"topic": " ", "period": "p", "id": "one"}, {"topic": "t", "period": "p", "id": "　"}]:
        assert request(app, "POST", "/api/sources/article", body) == (400, {"error": "请指定周报、期次和报道。"})
    bad = request(app, "POST", "/api/sources/article", {"topic": "../outside", "period": "2020-W42-media", "id": "one"})
    assert bad == (400, {"error": "Invalid report identifier"})


def test_uncached_original_is_fetched_once_under_saved_settings(
    app: Console, conn: sqlite3.Connection, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    weekly_fixture(conn)
    code, week = request(app, "POST", "/api/sources/search/plan/weekly",
                         {"topic_id": "shanghai-esg", "week_end": "2020-10-18"})
    assert code == 200
    period = week["period"]
    reads: list[str] = []

    def fetch(url: str) -> dict[str, object]:
        reads.append(url)
        return {"paragraphs": ["正文。"], "final_url": url, "fetched_at": "2026-10-19T08:00:00Z"}

    monkeypatch.setattr(articles.fetch, "fetch_article", fetch)
    body = {"topic": "shanghai-esg", "period": period, "id": "one"}
    code, first = request(app, "POST", "/api/sources/article", body)
    assert code == 200
    assert first == {"cached": False, "fetched_at": "2026-10-19T08:00:00Z",
                     "final_url": "https://example.org/article", "paragraphs": ["正文。"]}
    assert request(app, "POST", "/api/sources/article", body)[1]["cached"] is True
    assert reads == ["https://example.org/article"]


def test_unreachable_and_unsupported_originals_answer_with_codes(
    app: Console, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    weekly_fixture(conn)
    period = request(app, "POST", "/api/sources/search/plan/weekly",
                     {"topic_id": "shanghai-esg", "week_end": "2020-10-18"})[1]["period"]
    body = {"topic": "shanghai-esg", "period": period, "id": "one"}

    def unsupported(url: str) -> dict[str, object]:
        raise UnsupportedBody()

    def unreachable(url: str) -> dict[str, object]:
        raise ConnectionError("https://user:secret@example.org/ refused")

    monkeypatch.setattr(articles.fetch, "fetch_article", unsupported)
    code, value = request(app, "POST", "/api/sources/article", body)
    assert (code, value["code"]) == (422, "unsupported_body")
    monkeypatch.setattr(articles.fetch, "fetch_article", unreachable)
    code, value = request(app, "POST", "/api/sources/article", body)
    assert code == 502
    assert value == {"error": "暂时无法连接报道来源，请稍后重试，或点击报道标题查看原文。", "code": "source_unavailable"}
