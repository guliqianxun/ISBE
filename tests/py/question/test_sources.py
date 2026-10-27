"""essaies as the paper source (`isbe.question.sources`): identity keys, the
paper record, author strings, retries, spacing, paging and lookups."""

import httpx
import pytest
import replay
from question_schemas import errors

from isbe.core import http, upstreams
from isbe.question import sources


class Script:
    """essaies answering from a script: one entry per request, a (status, body)
    pair or an exception to raise. Each request's query is kept."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.queries = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.queries.append(dict(request.url.params.multi_items()) | {"path": request.url.path})
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, httpx.Response):
            return step
        status, body = step
        return httpx.Response(status, json=body)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def essaies(transport, clock=None, **options) -> sources.Essaies:
    """A client of `transport`; an essaies without capabilities unless `features` is given."""
    clock = clock or Clock()
    options.setdefault("features", ())
    return sources.Essaies(
        http.Http(5, transport=httpx.MockTransport(transport)), "http://essaies.test/",
        sleep=clock.sleep, clock=clock, **options,
    )


NEW = ("search_cursor", "search_filters", "optional_fields", "full_references", "batch_lookup")


def test_identity_keys_are_openalex_doi_then_arxiv_without_version():
    work = {"id": "2002.00469v3", "work_id": 3003709258, "doi": "https://doi.org/10.1029/2020MS002203"}
    assert sources.identity_keys(work) == [
        "openalex:W3003709258", "doi:10.1029/2020ms002203", "arxiv:2002.00469",
    ]
    # An arXiv DOI names the arXiv id too; essaies sends work ids as strings in searches.
    assert sources.identity_keys({"work_id": "4225906496", "doi": "10.48550/arXiv.2202.11214"}) == [
        "openalex:W4225906496", "doi:10.48550/arxiv.2202.11214", "arxiv:2202.11214",
    ]
    assert sources.identity_keys({"id": "hep-th/9901001v2"}) == ["arxiv:hep-th/9901001"]
    assert sources.identity_keys({"id": "10.1038/S41586-021-03854-Z"}) == ["doi:10.1038/s41586-021-03854-z"]
    assert sources.identity_keys({"work_id": "W12", "doi": "not a doi", "arxiv_id": "nope"}) == ["openalex:W12"]
    assert sources.identity_keys({"work_id": 0, "doi": None}) == []


def test_paper_record_from_a_recorded_search_matches_the_contract():
    s = replay.sample("ml-medium-range-weather")
    body = s.essaies.find("GET", "/api/openalex/search", [("limit", "100"), ("q", "data-driven global weather model")])
    papers = [sources.paper(w) for w in body["json"]["items"]]
    assert all(p is not None for p in papers)
    for p in papers:
        wrapped = {"schema": "paper_set@1", "run_id": "r", "set_id": "s", "kind": "query", "label": "",
                   "made_by": None, "created_at": "2026-10-22T00:00:00Z",
                   "members": [{"paper": p, "provenance": [{"via": "search", "query_id": "q1", "rank": 1}]}]}
        assert errors("paper_set", wrapped) == [], p
    weatherbench = next(p for p in papers if p["key"] == "openalex:W3003709258")
    assert weatherbench["keys"] == ["openalex:W3003709258", "doi:10.1029/2020ms002203", "arxiv:2002.00469"]
    assert weatherbench["authors"][:2] == ["Stephan Rasp", "Peter D. Dueben"]
    assert weatherbench["in_archive"] is True and weatherbench["abstract"] is None


def test_paper_needs_a_title_and_an_identity():
    assert sources.paper({"work_id": 1, "title": "  "}) is None
    assert sources.paper({"title": "No identity"}) is None
    record = sources.paper({"id": "1506.04214", "title": "Conv\n LSTM", "authors": "Shi Xingjian; Chen Zhourong",
                            "year": 15, "has_pdf": 1})
    assert record["title"] == "Conv LSTM"
    assert record["year"] is None
    assert record["authors"] == ["Shi Xingjian", "Chen Zhourong"]
    assert record["url"] == "https://arxiv.org/abs/1506.04214"
    assert record["in_archive"] is True


@pytest.mark.parametrize(("raw", "names"), [
    ("Stephan Rasp, Peter D. Dueben", ["Stephan Rasp", "Peter D. Dueben"]),
    ("Bi, Kaifeng, Xie, Lingxi", ["Kaifeng Bi", "Lingxi Xie"]),
    ("Keisler, Ryan", ["Ryan Keisler"]),
    ("Sanchez-Gonzalez, Alvaro, Lam, Remi", ["Alvaro Sanchez-Gonzalez", "Remi Lam"]),
    ("Shi Xingjian; Gao Zhihan", ["Shi Xingjian", "Gao Zhihan"]),
    ("", []),
    (None, []),
])
def test_author_strings(raw, names):
    assert sources.authors(raw) == names


def test_search_retries_a_passing_failure_and_spaces_requests():
    clock = Clock()
    script = Script((503, {"detail": "busy"}), httpx.ReadTimeout("timed out"), (200, {"items": [{"id": "1"}]}))
    source = essaies(script, clock, interval=0.5, backoff=1.0)
    found = source.search("radar nowcasting")
    assert found.works == [{"id": "1"}] and found.status == 200
    assert script.queries[0] == {"q": "radar nowcasting", "limit": "100", "path": "/api/openalex/search"}
    # Backoff 1 s then 2 s; the interval is already over by then.
    assert clock.slept == [1.0, 2.0]
    script.steps.append((200, {"items": []}))
    source.search("again")
    assert clock.slept[-1] == 0.5


def test_search_gives_up_after_its_retries():
    script = Script(*[(502, {"detail": "OpenAlex API unreachable: timed out"})] * 3)
    with pytest.raises(sources.SourceError, match="HTTP 502: OpenAlex API unreachable") as caught:
        essaies(script).search("x")
    assert caught.value.status == 502
    assert not script.steps


def test_a_query_openalex_rejects_is_not_retried():
    script = Script((502, {"detail": "OpenAlex API unreachable: HTTP Error 400: Bad Request"}), (200, {"items": []}))
    with pytest.raises(sources.SourceError, match="HTTP Error 400"):
        essaies(script).search("What works?")
    assert len(script.steps) == 1
    with pytest.raises(sources.SourceError, match="without an items list"):
        essaies(Script((200, {"works": []}))).search("x")
    with pytest.raises(sources.SourceError, match="HTTP 422"):
        essaies(Script((422, {"detail": [{"msg": "bad"}]}))).search("x")


def test_search_pages_with_the_cursor_until_the_limit():
    def page(n, cursor):
        return 200, {"items": [{"id": f"{n}.{i}"} for i in range(100)], "meta": {"count": 999, "next_cursor": cursor}}

    script = Script(page(1, "c2"), page(2, "c3"), page(3, None))
    found = essaies(script, features=NEW).search("q", limit=250, year_from=2018)
    assert len(found.works) == 250 and len(found.bodies) == 3 and found.filtered
    assert [q["cursor"] for q in script.queries] == ["*", "c2", "c3"]
    assert [q["limit"] for q in script.queries] == ["100", "100", "50"]
    assert script.queries[0] == {"q": "q", "include": "abstract,keywords", "year_from": "2018", "limit": "100",
                                 "cursor": "*", "path": "/api/openalex/search"}
    # An essaies without paging or filters: one page, no cursor, the years left to the caller.
    script = Script(page(1, "ignored"))
    found = essaies(script).search("q", limit=250, year_from=2018)
    assert len(found.works) == 100 and not found.filtered
    assert script.queries == [{"q": "q", "limit": "100", "path": "/api/openalex/search"}]


def test_capabilities_are_asked_once_and_an_old_essaies_has_none():
    script = Script((200, {"api_version": "0.5.0", "capabilities": ["batch_lookup", 7]}), (200, {"items": []}))
    source = essaies(script, features=None)
    assert source.features() == {"batch_lookup"}
    source.search("q")
    assert [q["path"] for q in script.queries] == ["/api/openalex/capabilities", "/api/openalex/search"]
    assert essaies(Script((404, {"detail": "Not Found"})), features=None).features() == frozenset()
    # One that cannot say is not taken for an old one: it raises, and the next use asks again.
    script = Script((500, {}), (200, {"capabilities": ["batch_lookup"]}))
    flaky = essaies(script, features=None, retries=0)
    with pytest.raises(sources.SourceError, match="HTTP 500"):
        flaky.features()
    assert flaky.features() == {"batch_lookup"}
    assert [q["path"] for q in script.queries] == ["/api/openalex/capabilities"] * 2


def test_failed_attempts_count_toward_the_stop_retries_included():
    # One request that fails all three attempts is enough: essaies is down, and the
    # next request fails at once instead of waiting out its own three.
    clock = Clock()
    script = Script(httpx.ReadTimeout("timed out"), httpx.ReadTimeout("timed out"), httpx.ReadTimeout("timed out"))
    source = essaies(script, clock)
    with pytest.raises(sources.Unreachable, match="failed 3 attempts in a row; the last: essaies could not be reached"):
        source.search("a")
    with pytest.raises(sources.Unreachable):
        source.search("b")
    assert len(script.queries) == 3 and clock.slept == [2.0, 4.0]
    assert upstreams.last(upstreams.ESSAIES).error == "ReadTimeout"


def test_health_hears_only_the_kind_of_failure_never_essaies_reply():
    body = "<html>502 Bad Gateway: upstream essaies-backend.corp.internal:8443 (10.1.2.3) timed out</html>"
    source = essaies(Script(httpx.Response(503, text=body)), retries=0)
    with pytest.raises(sources.SourceError, match="corp.internal"):  # the run's own error keeps it
        source.search("a")
    assert upstreams.last(upstreams.ESSAIES).error == "HTTP 503"


def test_a_connection_that_fails_is_not_retried_and_stops_the_client():
    clock = Clock()
    script = Script(httpx.ConnectError("[Errno 113] No route to host"))
    source = essaies(script, clock)
    with pytest.raises(sources.Unreachable, match="could not be reached: .Errno 113. No route to host"):
        source.search("a")
    # Every later request fails at once, without the network.
    with pytest.raises(sources.Unreachable, match="No route to host"):
        source.search("b")
    with pytest.raises(sources.Unreachable):
        source.lookup("2001.00001")
    assert len(script.queries) == 1 and clock.slept == []
    assert upstreams.last(upstreams.ESSAIES).ok is False


def test_three_failed_requests_in_a_row_count_as_down():
    script = Script(*[(503, {"detail": "busy"})] * 3, (200, {"items": []}), *[(502, {"detail": "timed out"})] * 3)
    source = essaies(script, retries=0)
    for _ in range(2):
        with pytest.raises(sources.SourceError) as caught:
            source.search("x")
        assert not isinstance(caught.value, sources.Unreachable)
    with pytest.raises(sources.Unreachable, match="failed 3 attempts in a row; the last: essaies answered HTTP 503"):
        source.search("x")
    with pytest.raises(sources.Unreachable):
        source.search("y")
    assert len(script.queries) == 3
    # An answer in between starts the count again; a 4xx about one query is an answer.
    script = Script((503, {}), (503, {}), (200, {"items": []}), (503, {}), (422, {"detail": "bad"}), (503, {}), (503, {}))
    source = essaies(script, retries=0)
    for expected in (sources.SourceError, sources.SourceError, None, sources.SourceError, sources.SourceError,
                     sources.SourceError, sources.SourceError):
        if expected is None:
            source.search("ok")
            continue
        with pytest.raises(expected) as caught:
            source.search("x")
        assert not isinstance(caught.value, sources.Unreachable)
    assert upstreams.last(upstreams.ESSAIES).ok is False


def test_lookups_one_at_a_time_stop_when_essaies_goes():
    script = Script((200, {"work": {"id": "1"}}), httpx.ConnectError("refused"))
    found, missing, failed = essaies(script).lookup_many(["1", "2", "3", "4"])
    assert list(found) == ["1"] and missing == []
    assert list(failed) == ["2", "3", "4"] and "could not be reached: refused" in failed["4"]
    assert len(script.queries) == 2


def test_lookups_when_the_capabilities_fail_are_failures_not_single_lookups():
    script = Script(*[(503, {})] * 3)
    found, missing, failed = essaies(script, features=None).lookup_many(["1", "2"])
    assert (found, missing, list(failed)) == ({}, [], ["1", "2"])
    assert [q["path"] for q in script.queries] == ["/api/openalex/capabilities"] * 3


def test_check_asks_once_without_retries():
    script = Script((503, {"detail": "starting"}))
    source = essaies(script, features=None)
    with pytest.raises(sources.SourceError, match="HTTP 503"):
        source.check()
    assert len(script.queries) == 1 and source.retries == sources.RETRIES
    script.steps.append((404, {"detail": "Not Found"}))
    source.check()  # an essaies from before capabilities answers, so it is up
    assert source.features() == frozenset()
    with pytest.raises(sources.Unreachable):
        essaies(Script(httpx.ConnectError("refused")), features=None).check()


def test_batch_lookup_in_hundreds():
    ids = [f"2001.{i:05}" for i in range(150)]
    first = {"items": [{"query": i, "work": {"id": i}, "cites": [], "cited_by": []} for i in ids[:98]],
             "missing": [ids[98]], "errors": [{"query": ids[99], "error": "mirror timeout"}]}
    script = Script((200, first), (503, {}), (503, {}), (503, {}))
    found, missing, failed = essaies(script, features=NEW).lookup_many(ids)
    assert len(found) == 98 and found[ids[0]] == {"work": {"id": ids[0]}, "cites": [], "cited_by": []}
    assert missing == [ids[98]]
    assert failed[ids[99]] == "mirror timeout"
    assert set(failed) == {ids[99], *ids[100:]}
    assert [q["path"] for q in script.queries] == ["/api/openalex/batch"] * 4
    # An id the reply does not mention at all counts as not known.
    script = Script((200, {"items": [], "missing": [], "errors": []}))
    assert essaies(script, features=NEW).lookup_many(["2001.00001"]) == ({}, ["2001.00001"], {})


def test_lookups_one_at_a_time():
    work = {"work": {"id": "2002.00469", "work_id": 3003709258}, "cites": [], "cited_by": []}
    script = Script((200, work), (404, {"detail": "not found"}), (500, {"detail": "x"}), (500, {}), (500, {}))
    found, missing, failed = essaies(script, retries=2).lookup_many(["2002.00469", "10.1/none", "9999.99999", "2002.00469"])
    assert found == {"2002.00469": work}
    assert missing == ["10.1/none"]
    assert list(failed) == ["9999.99999"] and "HTTP 500" in failed["9999.99999"]
    assert [q["q"] for q in script.queries] == ["2002.00469", "10.1/none"] + ["9999.99999"] * 3


def test_recorded_lookup_replays_through_the_client():
    s = replay.sample("ml-medium-range-weather")
    source = sources.Essaies(http.Http(5, transport=s.essaies.transport()), "http://essaies.test")
    record = source.lookup("2002.00469")
    assert sources.paper(record["work"])["key"] == "openalex:W3003709258"
    assert all(sources.paper(c) for c in record["cites"])


def test_from_settings_needs_the_essaies_url(monkeypatch):
    from isbe.core.errors import HttpError

    with pytest.raises(HttpError, match="ESSAIES_URL"):
        sources.Essaies.from_settings()
    monkeypatch.setenv("ESSAIES_URL", "http://essaies.test/")
    assert sources.Essaies.from_settings().base == "http://essaies.test"


def test_recorded_batch_lookup_of_the_must_find_papers():
    s = replay.sample("ml-medium-range-weather")
    source = sources.Essaies(http.Http(5, transport=s.essaies.transport()), "http://essaies.test")
    assert "batch_lookup" in source.features()
    ids = [i for p in s.question["must_find"] for key in p["keys"] if (i := replay.lookup_id(key))]
    found, missing, failed = source.lookup_many(ids)
    assert failed == {}
    assert set(found) | set(missing) == set(ids) and found
    assert all(sources.paper(record["work"]) for record in found.values())
    assert sum(1 for k in s.essaies.served if k.startswith("POST /api/openalex/batch")) == 1
