from datetime import UTC, datetime

import httpx
import pytest

from isbe.collect import arxiv
from isbe.collect.items import CollectError
from isbe.core import http
from isbe.topics import Arxiv

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>ArXiv Query</title><id>http://arxiv.org/api/q</id><updated>2026-10-12T00:00:00Z</updated>
  <entry>
    <id>http://arxiv.org/abs/2610.01234v2</id>
    <updated>2026-10-11T17:00:00Z</updated><published>2026-10-01T10:00:00Z</published>
    <title>Radar  Nowcasting
      with Diffusion</title>
    <summary>  We forecast rain.  </summary>
    <author><name>A. Author</name></author><author><name>B. Author</name></author>
    <category term="physics.ao-ph" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.LG" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2609.00001v1</id>
    <updated>2026-09-01T00:00:00Z</updated><title>Too old</title>
  </entry>
</feed>"""
SINCE = datetime(2026, 10, 6, tzinfo=UTC)


def test_parses_entries_inside_the_window() -> None:
    items = arxiv.parse(ATOM.encode(), SINCE)
    assert len(items) == 1
    p = items[0]
    assert (p.id, p.title, p.published_at) == (
        "arxiv:2610.01234",
        "Radar Nowcasting with Diffusion",
        "2026-10-11T17:00:00Z",
    )
    assert p.summary == "We forecast rain."
    assert p.authors == ["A. Author", "B. Author"]
    assert p.extra["categories"] == ["physics.ao-ph", "cs.LG"]
    assert arxiv.arxiv_id("http://arxiv.org/abs/math/0601001v1") == "math/0601001"
    assert arxiv.arxiv_id("http://arxiv.org/abs/2610.01234") == "2610.01234"


def test_api_errors_are_not_empty_search_results() -> None:
    error = """<feed xmlns="http://www.w3.org/2005/Atom">
          <title>ArXiv Query</title><id>http://arxiv.org/api/query</id>
          <updated>2026-10-12T00:00:00Z</updated>
          <entry><id>http://arxiv.org/api/errors#incorrect_id_format</id>
            <title>Error</title><summary>incorrect id format</summary>
            <updated>2026-10-01T00:00:00Z</updated>
          </entry>
        </feed>"""
    with pytest.raises(CollectError, match="incorrect id format"):
        arxiv.parse(error.encode(), SINCE)
    start = error.index("<entry>")
    empty = error[:start] + error[error.index("</entry>") + len("</entry>") :]
    assert arxiv.parse(empty.encode(), SINCE) == []


def test_query_quotes_phrases() -> None:
    a = Arxiv(categories=["cs.CV", "eess.IV"], include_keywords=["radar echo", " "], max_results=5)
    assert arxiv.query(a) == '(cat:cs.CV OR cat:eess.IV) AND (ti:"radar echo" OR abs:"radar echo")'
    quoted = Arxiv(categories=["cs.CV"], include_keywords=['say "hi" \\'])
    assert arxiv.query(quoted) == '(cat:cs.CV) AND (ti:"say \\"hi\\" \\\\" OR abs:"say \\"hi\\" \\\\")'


def test_fetch_asks_newest_first_and_pauses_for_the_api_terms() -> None:
    seen: list[httpx.Request] = []

    def api(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=ATOM.encode())

    pauses: list[float] = []
    client = http.Http(5, transport=httpx.MockTransport(api))
    a = Arxiv(categories=["cs.CV"], include_keywords=["radar"], max_results=7)
    items = arxiv.fetch(client, "https://export.arxiv.org/api/query", a, SINCE, sleep=pauses.append)
    assert [i.id for i in items] == ["arxiv:2610.01234"] and pauses == [3]
    params = dict(seen[0].url.params)
    assert params == {
        "search_query": '(cat:cs.CV) AND (ti:"radar" OR abs:"radar")',
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": "7",
    }
    failing = http.Http(5, transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(CollectError, match="^http status: 503$"):
        arxiv.fetch(failing, "https://export.arxiv.org/api/query", a, SINCE, sleep=pauses.append)
    assert pauses == [3], "a failed request does not wait"
