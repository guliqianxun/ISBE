"""`isbe serve` mounts the source routes, the media briefing's article list and the queue."""

from pathlib import Path
from typing import Any

import pytest

import isbe.web
from isbe import cli
from isbe.generation import Manager
from isbe.reports import media
from isbe.web.routes_sources import api


def test_serve_mounts_sources_articles_and_the_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(isbe.web, "serve", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert cli.main(["--data", str(tmp_path), "serve", "--bind", "127.0.0.1:8099"]) == 0
    (args, kwargs), = calls
    assert args == (tmp_path, "127.0.0.1:8099", False, False)
    assert kwargs == {"generation": Manager.open, "articles": media.articles, "sources": api}
