"""The original-article cache: reuse, validation, failure mapping and the stored row format."""

from pathlib import Path
from typing import Any

import pytest

from isbe.articles import cache
from isbe.articles.cache import (
    MAX_PARAGRAPHS,
    MAX_TEXT,
    FetchFailed,
    caused_by,
    get_or_fetch_with,
    load,
    validated,
)
from isbe.articles.fetch import UnsupportedBody
from isbe.core import db

URL = "https://example.org/article"


def article() -> dict[str, Any]:
    return {"paragraphs": ["完整报道的第一段。", "原文下一段。"], "final_url": URL, "fetched_at": "2026-10-19T08:00:00Z"}


def never() -> dict[str, Any]:
    raise AssertionError("cached reads must not fetch")


def test_cached_read_survives_reopen_and_does_not_fetch_again(tmp_path: Path) -> None:
    path = tmp_path / "isbe.db"
    conn = db.connect(path)
    assert load(conn, URL) is None
    first = get_or_fetch_with(conn, URL, article)
    assert first["cached"] is False
    conn.close()
    conn = db.connect(path)
    second = get_or_fetch_with(conn, URL, never)
    assert second["cached"] is True
    assert (second["paragraphs"], second["fetched_at"]) == (first["paragraphs"], first["fetched_at"])
    # The row holds the validated record as JSON: sorted keys, compact separators, raw UTF-8.
    row = conn.execute("SELECT content FROM article_text WHERE url = ?", (URL,)).fetchone()
    assert row[0] == (
        '{"fetched_at":"2026-10-19T08:00:00Z","final_url":"https://example.org/article",'
        '"paragraphs":["完整报道的第一段。","原文下一段。"]}'
    )


def test_failed_or_invalid_reads_never_cache_and_can_retry() -> None:
    conn = db.connect(":memory:")

    def down() -> dict[str, Any]:
        raise RuntimeError("source down")

    with pytest.raises(FetchFailed) as raised:
        get_or_fetch_with(conn, URL, down)
    assert str(raised.value) == "original article could not be read"
    assert load(conn, URL) is None
    for invalid in [
        {"paragraphs": []},
        {"paragraphs": [42]},
        {"paragraphs": [" "]},
        {"paragraphs": ["x" * (MAX_TEXT + 1)]},
        {"paragraphs": ["x"] * (MAX_PARAGRAPHS + 1)},
        {"paragraphs": ["text"], "final_url": "javascript:alert(1)", "fetched_at": "2026-10-19T08:00:00Z"},
        {"paragraphs": ["text"], "final_url": URL, "fetched_at": "not a date"},
    ]:
        with pytest.raises(ValueError):
            get_or_fetch_with(conn, URL, lambda: invalid)
        assert load(conn, URL) is None
    assert get_or_fetch_with(conn, URL, article)["cached"] is False


def test_validation_messages_are_stable() -> None:
    for value, message in [
        ([], "article paragraphs are missing"),
        ({"paragraphs": ["x"]}, "article URL is missing"),
        ({"paragraphs": ["x"], "final_url": URL}, "article retrieval time is missing"),
        ({**article(), "fetched_at": "2026-10-19"}, "invalid article retrieval time"),
        # A plain error, as reader::validate_url's bail!, not an HTTP 400.
        ({**article(), "final_url": "https://user@example.org/"}, "without credentials"),
    ]:
        with pytest.raises(ValueError, match=message):
            validated(value)


def test_original_title_survives_cache_and_old_entries_remain_readable() -> None:
    conn = db.connect(":memory:")
    get_or_fetch_with(conn, URL, lambda: {**article(), "title": "原报道完整标题"})
    saved = load(conn, URL)
    assert saved is not None and saved["title"] == "原报道完整标题"
    assert "title" not in validated(article())
    for title in [None, 42, " ", "x" * 2049]:
        old = {**article(), "title": title}
        if title is None:
            assert "title" not in validated(old)
        else:
            with pytest.raises(ValueError):
                validated(old)


def test_invalid_cached_text_is_not_displayed_or_silently_overwritten() -> None:
    conn = db.connect(":memory:")
    conn.execute("INSERT INTO article_text VALUES (?1, ?2)", (URL, "{bad"))
    with pytest.raises(ValueError):
        load(conn, URL)
    with pytest.raises(ValueError):
        get_or_fetch_with(conn, URL, never)
    row = conn.execute("SELECT content FROM article_text WHERE url=?1", (URL,)).fetchone()
    assert row[0] == "{bad"


def test_a_concurrent_first_read_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = db.connect(":memory:")

    def racing() -> dict[str, Any]:
        # Another request stores its read while this one is fetching.
        get_or_fetch_with(conn, URL, article)
        return {**article(), "fetched_at": "2026-10-20T08:00:00Z"}

    result = get_or_fetch_with(conn, URL, racing)
    assert (result["cached"], result["fetched_at"]) == (True, "2026-10-19T08:00:00Z")


def test_unsupported_bodies_stay_recognizable_through_fetch_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = db.connect(":memory:")

    def unsupported(url: str) -> dict[str, Any]:
        raise UnsupportedBody() from ValueError("Article body is empty")

    monkeypatch.setattr(cache.fetch, "fetch_article", unsupported)
    with pytest.raises(FetchFailed) as raised:
        cache.get_or_fetch(conn, URL)
    # The web layer answers 422 for this chain: the original error stays reachable through its causes.
    assert caused_by(raised.value, UnsupportedBody) is not None
    assert caused_by(raised.value, KeyError) is None
