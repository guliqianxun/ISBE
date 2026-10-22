from collections.abc import Callable
from http.server import BaseHTTPRequestHandler

import httpx
import pytest

from isbe.collect import CollectError, Endpoints, collect
from isbe.core import db, http, jsonfmt, netguard
from isbe.topics import Topic

ROW = {"title": "Radar", "abstract": "Rain.", "authors": "A", "categories": "cs.CV"}


def essaies_and_github(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/search":
        rows = [dict(ROW, id=f"2610.0000{n}", update_date="2099-01-01") for n in range(3)]
        return httpx.Response(200, json={"items": rows})
    if request.url.path == "/repos/a/ok":
        return httpx.Response(200, json={"created_at": "2020-01-01T00:00:00Z"})
    return httpx.Response(404)


def topic(**blocks: object) -> Topic:
    return Topic.model_validate({"id": "t", "label": "T", "cadence": "weekly", **blocks})


ENDPOINTS = Endpoints(essaies="http://essaies.lan", arxiv_api="http://arxiv.lan/api/query",
                      github_api="http://api.github.lan")


def test_stats_and_stored_items() -> None:
    conn = db.connect(":memory:")
    client = http.Http(5, transport=httpx.MockTransport(essaies_and_github))
    stats: dict[str, object] = {}
    t = topic(arxiv={"categories": ["cs.CV"], "include_keywords": ["radar"]})
    collect(conn, t, ENDPOINTS, stats, client=client)
    assert jsonfmt.dumps(stats) == '{"found":{"essaies":3},"new":3}'
    stats = {}
    collect(conn, t, ENDPOINTS, stats, client=client)
    assert stats == {"found": {"essaies": 3}, "new": 0}
    assert conn.execute("SELECT count(*) FROM topic_items WHERE topic_id = 't'").fetchone()[0] == 3


def test_arxiv_api_is_used_without_essaies() -> None:
    seen: list[str] = []

    def api(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(200, content=b'<feed xmlns="http://www.w3.org/2005/Atom"><title>q</title></feed>')

    conn = db.connect(":memory:")
    stats: dict[str, object] = {}
    pauses: list[float] = []
    collect(conn, topic(arxiv={"categories": ["cs.CV"]}), Endpoints(None, "http://arxiv.lan/q", "x"),
            stats, client=http.Http(5, transport=httpx.MockTransport(api)), sleep=pauses.append)
    assert (seen, pauses, stats) == (["arxiv.lan"], [3], {"found": {"arxiv": 0}, "new": 0})


def test_failures_are_listed_after_the_rest_is_stored(
    serve, local_fetcher: Callable[..., netguard.PublicFetcher]
) -> None:
    def blocked(req: BaseHTTPRequestHandler) -> None:
        serve.reply(req, 200, "<!DOCTYPE html><html><body>captcha</body></html>")

    port = serve(blocked).rsplit(":", 1)[1]
    conn = db.connect(":memory:")
    client = http.Http(5, transport=httpx.MockTransport(essaies_and_github))
    t = topic(
        arxiv={"categories": ["cs.CV"], "include_keywords": ["radar"]},
        rss={"feeds": [{"name": "blog", "url": f"http://blog.invalid:{port}/feed"}]},
        github={"repos": ["a/ok", "a/gone"]},
        zhihu={"queries": ["雷达"]},
    )
    stats: dict[str, object] = {}
    with pytest.raises(CollectError) as failure:
        collect(conn, t, ENDPOINTS, stats, client=client, feeds=local_fetcher(timeout=5))
    assert str(failure.value) == (
        "rss:blog: got an HTML page, not a feed (the site blocks this client, or the URL is not a feed); "
        "gh:a/gone: http status: 404"
    )
    assert stats == {"not_yet_supported": ["zhihu"], "found": {"essaies": 3, "github": 1}, "new": 4}
    assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 4
