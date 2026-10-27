"""The OpenAlex API as the paper source (`isbe.question.openalex`, #121): the works
it returns as paper records, the requests it sends, its retries and quota, and
the citation stage on it. Works are real answers recorded from api.openalex.org
(tests/fixtures/question/openalex/works.json)."""

import json
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from question_schemas import errors

from isbe.core import db, http, settings, upstreams
from isbe.question import citations, openalex, problems, sources

WORKS = json.loads((Path(__file__).parents[2] / "fixtures/question/openalex/works.json").read_text())["works"]
WEATHERBENCH, KEISLER, CONVLSTM, SERVED = WORKS


class OpenAlexFake:
    """The OpenAlex API answering from the recorded works: `/`, `/works/<id>`,
    `/works?filter=…` and `/works?search=…`. `script` answers come first."""

    def __init__(self, works=WORKS, pages=None):
        self.works = {w["id"].rsplit("/", 1)[1]: w for w in works}
        self.pages = pages or []  # search pages, in order
        self.script: list = []
        self.requests: list[httpx.URL] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url)
        if self.script:
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step
        path, params = request.url.path, request.url.params
        if path == "/":
            return httpx.Response(200, json={"documentation_url": "https://docs.openalex.org"})
        if path.startswith("/works/"):
            ident = path.removeprefix("/works/")
            found = self.works.get(ident) or next(
                (w for w in self.works.values() if ident.startswith("doi:") and (w.get("doi") or "").lower().endswith(
                    ident[4:].lower())), None)
            return httpx.Response(200, json=found) if found else httpx.Response(404, json={"error": "not found"})
        if "search" in params:
            page = self.pages.pop(0)
            return httpx.Response(200, json=page)
        field, _, values = params["filter"].partition(":")
        wanted = values.split("|")
        if field == "ids.openalex":
            results = [self.works[v] for v in wanted if v in self.works]
        elif field == "doi":
            results = [w for w in self.works.values() if (w.get("doi") or "").lower().removeprefix(
                "https://doi.org/") in wanted]
        elif field == "cites":
            results = [w for w in self.works.values() if f"https://openalex.org/{values}" in w.get("referenced_works", [])]
        else:
            raise AssertionError(field)
        return httpx.Response(200, json={"meta": {"count": len(results)}, "results": results})


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def client(fake, clock=None, **options) -> openalex.OpenAlex:
    clock = clock or Clock()
    return openalex.OpenAlex(http.Http(5, transport=httpx.MockTransport(fake)), "https://api.openalex.test",
                             sleep=clock.sleep, clock=clock, **options)


def test_a_recorded_work_becomes_a_paper_record_of_the_contract():
    keisler = sources.paper(openalex.work(KEISLER))
    assert keisler["keys"] == ["openalex:W4221149294", "doi:10.48550/arxiv.2202.07575", "arxiv:2202.07575"]
    assert keisler["title"] == "Forecasting Global Weather with Graph Neural Networks" and keisler["year"] == 2022
    assert keisler["authors"] == ["Ryan Keisler"]
    # The abstract comes back from OpenAlex's inverted index, in word order.
    assert keisler["abstract"].startswith("We present a data-driven approach for forecasting global weather")
    assert "graph neural networks" in keisler["keywords"]
    weatherbench = sources.paper(openalex.work(WEATHERBENCH))
    assert weatherbench["key"] == "openalex:W3003709258" and weatherbench["venue"] == (
        "Journal of Advances in Modeling Earth Systems")
    assert weatherbench["authors"][:2] == ["Stephan Rasp", "Peter D. Dueben"]
    for p in (keisler, weatherbench):
        wrapped = {"schema": "paper_set@1", "run_id": "r", "set_id": "s", "kind": "query", "label": "",
                   "made_by": None, "created_at": "2026-10-27T00:00:00Z",
                   "members": [{"paper": p, "provenance": [{"via": "search", "query_id": "q1", "rank": 1}]}]}
        assert errors("paper_set", wrapped) == [], p


def test_abstract_from_an_inverted_index():
    assert openalex.abstract({"world": [1], "Hello": [0], "again": [3], "hello": [2]}) == "Hello world hello again"
    assert openalex.abstract(None) is None and openalex.abstract({}) is None


def test_search_sends_the_query_years_key_and_mailto_and_pages_with_the_cursor():
    pages = [
        {"meta": {"count": 3, "per_page": 2, "next_cursor": "c2"}, "results": [WEATHERBENCH, KEISLER]},
        {"meta": {"count": 3, "per_page": 2, "next_cursor": "c3"}, "results": [CONVLSTM]},
    ]
    fake = OpenAlexFake(pages=pages)
    found = client(fake, key="oa-key", mailto="me@example.org").search(
        '"ensemble forecast" AND wind', limit=3, year_from=2015, year_to=2024)
    assert [w["work_id"] for w in found.works] == ["W3003709258", "W4221149294", "W1485009520"]
    assert found.filtered and found.status == 200
    first, second = (dict(u.params.multi_items()) for u in fake.requests)
    assert first["search"] == '"ensemble forecast" AND wind' and first["filter"] == "publication_year:>2014,publication_year:<2025"
    assert (first["cursor"], first["per_page"], second["cursor"], second["per_page"]) == ("*", "3", "c2", "1")
    assert (first["api_key"], first["mailto"]) == ("oa-key", "me@example.org")
    assert first["select"] == openalex.SELECT
    # What is stored of each reply: the paging and the works as the run reads them.
    assert found.bodies[0]["meta"] == {"count": 3, "per_page": 2, "next_cursor": "c2"}
    assert found.bodies[0]["items"][1]["arxiv_id"] == "2202.07575"


def test_without_key_or_mailto_nothing_extra_is_sent():
    fake = OpenAlexFake(pages=[{"meta": {"next_cursor": None}, "results": []}])
    client(fake).search("wind")
    assert "api_key" not in fake.requests[0].params and "mailto" not in fake.requests[0].params
    assert "filter" not in fake.requests[0].params


def test_a_passing_failure_is_retried_with_backoff_and_a_short_429_after_its_retry_after():
    fake = OpenAlexFake(pages=[{"meta": {}, "results": [KEISLER]}])
    fake.script = [httpx.Response(503, json={"error": "busy"}),
                   httpx.Response(429, headers={"Retry-After": "7"}, json={"error": "Too many requests"})]
    clock = Clock()
    found = client(fake, clock, interval=0).search("wind")
    assert len(found.works) == 1
    # 2 s backoff after the 503; the 429 asks for 7 s, more than the 4 s backoff.
    assert clock.slept == [2.0, 7.0]


def test_a_spent_daily_budget_fails_at_once_as_quota_and_openalex_counts_as_up():
    fake = OpenAlexFake()
    fake.script = [httpx.Response(429, headers={"Retry-After": "57076", "X-RateLimit-Remaining": "0"},
                                  json={"error": "Rate limit exceeded", "message": "Insufficient budget."})]
    clock = Clock()
    with pytest.raises(sources.SourceError) as caught:
        client(fake, clock).search("wind")
    error = caught.value
    assert len(fake.requests) == 1 and clock.slept == []
    assert str(error).startswith("OpenAlex quota used up: the shared keyless daily budget resets in 15.9 h; set "
                                 "OPENALEX_API_KEY")
    assert problems.source_code(error) == problems.OPENALEX_QUOTA
    # It answered, so it is not down, but health says the quota is spent.
    assert upstreams.last(upstreams.OPENALEX).error == "HTTP 429 (quota)"


def test_a_refused_key_and_no_connection_have_codes_of_their_own():
    fake = OpenAlexFake()
    fake.script = [httpx.Response(403, json={"error": "Invalid API key"})]
    with pytest.raises(sources.SourceError) as refused:
        client(fake, key="wrong").search("wind")
    assert problems.source_code(refused.value) == problems.OPENALEX_REJECTED
    assert "Check OPENALEX_API_KEY" in str(refused.value) and "wrong" not in str(refused.value)
    fake.script = [httpx.ConnectError("[Errno 101] Network is unreachable")]
    with pytest.raises(sources.Unreachable) as gone:
        client(fake).check()
    assert problems.source_code(gone.value) == problems.OPENALEX_UNREACHABLE
    assert upstreams.last(upstreams.OPENALEX).error == "ConnectError: [Errno 101] Network is unreachable"
    fake.script = [httpx.Response(502, text="bad gateway")] * 3
    with pytest.raises(sources.Unreachable) as down:
        client(fake).search("wind")
    assert problems.source_code(down.value) == problems.OPENALEX_DOWN


def test_lookup_many_finds_works_by_openalex_id_doi_and_arxiv_id():
    fake = OpenAlexFake()
    found, missing, failed = client(fake).lookup_many(
        ["W3003709258", "10.1029/2020MS002203", "2202.07575", "10.9999/none", "not an id"])
    assert set(found) == {"W3003709258", "10.1029/2020MS002203", "2202.07575"} and failed == {}
    assert missing == ["10.9999/none", "not an id"]
    # What each work cites comes with it, as OpenAlex ids.
    assert len(found["W3003709258"]["cites"]) == len(WEATHERBENCH["referenced_works"])
    assert found["W3003709258"]["cites"][0] == {"work_id": "W1485009520"}
    filters = [u.params["filter"] for u in fake.requests]
    assert filters == ["ids.openalex:W3003709258",
                       "doi:10.1029/2020ms002203|10.48550/arxiv.2202.07575|10.9999/none"]


def test_lookup_many_stops_when_openalex_is_gone():
    fake = OpenAlexFake()
    fake.script = [httpx.ConnectError("[Errno 111] Connection refused")]
    found, missing, failed = client(fake).lookup_many(["W1", "10.1/x"])
    assert found == {} and missing == [] and set(failed) == {"W1", "10.1/x"}
    assert len(fake.requests) == 1


def test_a_full_lookup_gives_the_cited_works_and_the_citing_ones():
    fake = OpenAlexFake()
    record = client(fake).lookup("W3003709258", scope="all", limit=100)
    assert record["work"]["title"].startswith("WeatherBench")
    titled = [w for w in record["cites"] if w.get("title")]
    # Only two of the 63 references are in the fake; the rest stay as bare ids.
    assert [w["work_id"] for w in titled] == ["W1485009520", "W1569592761"]
    assert len(record["cites"]) == 63
    # The work, its 63 references by id in two batches of `BATCH`, then its citing works.
    asked = [(u.path, u.params.get("filter", "").partition(":")[0], u.params.get("filter", "").count("|") + 1,
              u.params.get("sort")) for u in fake.requests]
    assert asked == [("/works/W3003709258", "", 1, None), ("/works", "ids.openalex", 50, None),
                     ("/works", "ids.openalex", 13, None), ("/works", "cites", 1, "cited_by_count:desc")]
    assert client(OpenAlexFake()).lookup("10.9999/none") is None


def test_the_citation_stage_on_openalex_finds_the_edges_between_candidates(conn):
    """Two candidates where one cites the other: the batch lookup's reference lists
    hold the edge, with no full lookup."""
    from test_citations import make_run

    run_id = make_run(conn, [sources.paper(openalex.work(w)) for w in (WEATHERBENCH, CONVLSTM)])
    fake = OpenAlexFake()
    result = citations.run(conn, client(fake), run_id, full_limit=0)
    assert result.errors == [] and result.looked_up == 2
    assert citations.edges(conn, run_id)["citations"] == [["openalex:W3003709258", "openalex:W1485009520"]]
    assert [u.params["filter"] for u in fake.requests] == ["ids.openalex:W3003709258|W1485009520"]


def test_the_settings_choose_the_source(monkeypatch):
    assert settings.paper_source(lambda name: None) == "openalex"
    assert settings.paper_source({"ESSAIES_URL": "http://e"}.get) == "essaies"
    assert settings.paper_source({"ESSAIES_URL": "http://e", "ISBE_PAPER_SOURCE": "openalex"}.get) == "openalex"
    monkeypatch.setenv("OPENALEX_API_KEY", "oa-key")
    monkeypatch.setenv("OPENALEX_MAILTO", "me@example.org")
    source = sources.from_settings()
    assert isinstance(source, openalex.OpenAlex) and source.base == "https://api.openalex.org"
    assert source._params(None) == [("api_key", "oa-key"), ("mailto", "me@example.org")]
    with pytest.raises(Exception, match="arXiv engine searches essaies"):
        sources.from_settings("arxiv")
    monkeypatch.setenv("ISBE_PAPER_SOURCE", "essaies")
    monkeypatch.setenv("ESSAIES_URL", "http://essaies.test")
    assert isinstance(sources.from_settings("arxiv"), sources.Essaies)


@pytest.mark.parametrize(("name", "value", "ok"), [
    ("ISBE_PAPER_SOURCE", "openalex", True), ("ISBE_PAPER_SOURCE", "OpenAlex", False),
    ("OPENALEX_MAILTO", "me@example.org", True), ("OPENALEX_MAILTO", "a@b.org, c@d.org", False),
    ("OPENALEX_API_KEY", "abc123", True), ("OPENALEX_API_KEY", "abc 123", False),
    ("OPENALEX_API_URL", "https://api.openalex.org", True), ("OPENALEX_API_URL", "ftp://x", False),
])
def test_the_settings_are_checked(name, value, ok):
    if ok:
        assert settings.validate(name, value) == value
    else:
        with pytest.raises(settings.Invalid):
            settings.validate(name, value)
    assert "OPENALEX_API_KEY" in settings.SECRETS


@pytest.fixture
def conn():
    with closing(db.connect(":memory:")) as c:
        yield c


def test_one_year_bound_is_a_one_sided_filter():
    fake = OpenAlexFake(pages=[{"meta": {}, "results": []}, {"meta": {}, "results": []}])
    client(fake).search("wind", year_from=2015)
    client(fake).search("wind", year_to=2020)
    assert [u.params["filter"] for u in fake.requests] == ["publication_year:>2014", "publication_year:<2021"]


def test_a_batch_reads_a_whole_page_and_odd_dois_are_looked_up_alone():
    odd = "10.1002/(sici)1097-0258(19980615)17:11<1185::aid-sim850>3.0.co;2-#"
    comma = "10.1000/a,b"
    twin = dict(WEATHERBENCH, id="https://openalex.org/W9")  # a second work under the same DOI
    fake = OpenAlexFake(works=[*WORKS, twin])
    found, missing, failed = client(fake).lookup_many(["10.1029/2020ms002203", odd, comma])
    assert "10.1029/2020ms002203" in found and failed == {} and missing == [odd, comma]
    batch, single = fake.requests
    assert batch.params["per_page"] == "200"
    # The SICI DOI goes in the filter as a query value (httpx escapes its "#");
    # the one with a comma would split the OR filter, so it is looked up alone.
    assert batch.params["filter"] == f"doi:10.1029/2020ms002203|{odd}"
    assert single.raw_path.decode().startswith("/works/doi:10.1000/a%2Cb")
    # A DOI in a path is escaped too.
    assert openalex._path(odd) == "/works/doi:10.1002/%28sici%291097-0258%2819980615%2917%3A11%3C1185%3A%3Aaid-sim850%3E3.0.co%3B2-%23"


def test_a_short_429_holds_off_every_thread():
    fake = OpenAlexFake(pages=[{"meta": {}, "results": []}])
    fake.script = [httpx.Response(429, headers={"Retry-After": "5"}, json={"error": "Too many requests"})]
    clock = Clock()
    source = client(fake, clock, interval=0)
    source.search("wind")
    # The next request of any thread waits until the 5 s are over.
    assert source._next >= 5.0


def test_without_a_key_a_run_looks_up_at_most_keyless_full_citations(conn, monkeypatch):
    from test_citations import make_run

    monkeypatch.setattr(openalex, "KEYLESS_FULL", 1)
    papers = [sources.paper(openalex.work(w)) for w in (WEATHERBENCH, CONVLSTM)]
    run_id = make_run(conn, papers)
    fake = OpenAlexFake()
    citations.run(conn, client(fake), run_id)
    # The most cited kept paper only (ConvLSTM).
    assert [u.path for u in fake.requests if u.path.startswith("/works/")] == ["/works/W1485009520"]
    fake = OpenAlexFake()
    with closing(db.connect(":memory:")) as other:
        citations.run(other, client(fake, key="k"), make_run(other, papers))
    assert len([u for u in fake.requests if u.path.startswith("/works/")]) == 2
