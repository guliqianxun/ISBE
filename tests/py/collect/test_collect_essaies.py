from datetime import UTC, datetime

import httpx
import pytest

from isbe.collect import essaies
from isbe.collect.items import CollectError
from isbe.core import http
from isbe.topics import Arxiv


def test_parses_search_rows() -> None:
    items = essaies.parse({"items": [{
        "id": "2610.00001", "title": "Radar\n  nowcasting", "authors": "A One; B Two",
        "abstract": "Rain.", "update_date": "2026-10-10", "categories": "physics.ao-ph cs.LG",
        "has_pdf": 1, "path": "/srv/pdf/2026/2610.00001.pdf", "rank": -3.2, "year": 2026,
    }]})
    p = items[0]
    assert (p.id, p.title, p.published_at) == ("arxiv:2610.00001", "Radar nowcasting", "2026-10-10T00:00:00Z")
    assert p.authors == ["A One", "B Two"]
    assert p.extra["categories"] == ["physics.ao-ph", "cs.LG"]
    assert p.extra["path"] == "/srv/pdf/2026/2610.00001.pdf"
    assert "rank" not in p.extra
    assert p.extra == {"categories": ["physics.ao-ph", "cs.LG"], "has_pdf": 1,
                       "path": "/srv/pdf/2026/2610.00001.pdf", "year": 2026}
    with pytest.raises(CollectError, match="too old"):
        essaies.parse({"items": [{"id": "2610.00001", "title": "t"}]})


def test_rejected_rows_name_the_problem() -> None:
    with pytest.raises(CollectError, match="^essaies response has no items array$"):
        essaies.parse({"rows": []})
    with pytest.raises(CollectError, match="^essaies item is not an object$"):
        essaies.parse({"items": [1]})
    with pytest.raises(CollectError, match="^essaies item without id$"):
        essaies.parse({"items": [{"id": 7}]})
    with pytest.raises(CollectError, match='^2610.1: bad update_date "10/10/2026"$'):
        essaies.parse({"items": [{"id": "2610.1", "update_date": "10/10/2026"}]})
    timestamp = essaies.parse({"items": [{"id": "1", "update_date": "2026-10-10T08:00:00"}]})
    assert timestamp[0].published_at == "2026-10-10T00:00:00Z"


def test_keywords_become_fts_phrases() -> None:
    assert essaies.fts_query(["text-to-video", "QPE", " - "]) == '"text to video" OR "QPE"'


def test_fetch_sends_the_window_and_explains_an_old_server() -> None:
    seen: list[httpx.Request] = []

    def server(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": []})

    a = Arxiv(categories=["cs.CV", "cs.LG"], include_keywords=["radar echo"])
    since = datetime(2026, 10, 6, tzinfo=UTC)
    client = http.Http(5, transport=httpx.MockTransport(server))
    assert essaies.fetch(client, "http://essaies.lan:8080//", a, since) == []
    assert seen[0].url.path == "/api/search"
    assert dict(seen[0].url.params) == {
        "q": '"radar echo"', "since": "2026-10-06", "categories": "cs.CV,cs.LG",
        "sort": "date", "limit": "2000",
    }
    old = http.Http(5, transport=httpx.MockTransport(lambda r: httpx.Response(422)))
    with pytest.raises(CollectError, match=r"essaies rejected the search \(422\)"):
        essaies.fetch(old, "http://essaies.lan", a, since)
