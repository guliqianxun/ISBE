"""The recorded data of the sample questions in tests/fixtures/question: complete,
valid against the contracts, replayable through ISBE's HTTP client, free of
internal hosts, paths and secrets, and still matching live essaies (`live`)."""

import json
import os

import httpx
import pytest
import replay
from question_schemas import errors, samples

from isbe.core import http

IDS = [q["id"] for q in samples()]

# Must-find papers the recorded searches return, over all of a sample's queries
# (the question as asked finds none). Re-recording that changes these is a
# change in essaies or OpenAlex: check it before updating the numbers.
FOUND = {
    "radar-nowcasting-dl": 8,
    "ml-medium-range-weather": 9,
    "diffusion-image-generation": 7,
    "neural-pde-solvers": 5,
    "green-space-mental-health": 5,
}


def work_keys(work: dict) -> set[str]:
    keys = set()
    if work.get("work_id"):
        keys.add(f"openalex:W{work['work_id']}")
    if work.get("doi"):
        keys.add("doi:" + work["doi"].lower())
    if work.get("arxiv_id"):
        keys.add("arxiv:" + work["arxiv_id"])
    return keys


def found(question: dict, works: list[dict]) -> list[str]:
    keys = set().union(*map(work_keys, works)) if works else set()
    return [paper["title"] for paper in question["must_find"] if keys & set(paper["keys"])]


def search(sample: replay.Sample, query: str) -> dict:
    return sample.essaies.find("GET", "/api/openalex/search", [("limit", replay.SEARCH_LIMIT), ("q", query)])


def test_every_sample_question_has_recorded_data():
    assert replay.sample_ids() == sorted(IDS)
    assert set(FOUND) == set(IDS)


@pytest.mark.parametrize("sample_id", IDS)
def test_plan_is_a_valid_plan_for_its_question(sample_id):
    s = replay.sample(sample_id)
    assert errors("question_plan", s.plan) == []
    assert (s.plan["question"], s.plan["lang"]) == (s.question["question"], s.question["lang"])


@pytest.mark.parametrize("sample_id", IDS)
def test_cassette_holds_exactly_what_a_run_asks(sample_id):
    # The plain searches and lookups, plus what ISBE's client asks while it searches
    # with both plans and looks up the must-find papers.
    s = replay.sample(sample_id)
    plain = {replay.request_key("GET", path, query) for path, query in replay.essaies_requests(s.question, s.plan)}
    # Strata's plan only once its searches are recorded (see test_search.RECALL).
    plans = [s.plan, s.strata_plan()] if s.strata_recorded() else [s.plan]
    replay.client_run(s, "http://essaies.test", s.essaies.transport(), plans)
    recorded = {replay.request_key(i["request"]["method"], i["request"]["path"], map(tuple, i["request"]["query"]),
                                   i["request"].get("json"))
                for i in s.essaies.interactions}
    assert recorded == plain | set(s.essaies.served)


@pytest.mark.parametrize("sample_id", IDS)
def test_arxiv_cassette_holds_exactly_what_a_run_asks(sample_id):
    s = replay.sample(sample_id)
    cassette = s.arxiv
    if cassette is None:
        pytest.skip("no searches of essaies' arXiv index recorded for this sample")
    replay.client_run(s, "http://essaies.test", cassette.transport(), [s.strata_plan("arxiv")], "arxiv")
    recorded = {replay.request_key(i["request"]["method"], i["request"]["path"], map(tuple, i["request"]["query"]))
                for i in cassette.interactions}
    assert recorded == set(cassette.served)


@pytest.mark.parametrize("sample_id", IDS)
def test_plan_queries_find_the_recorded_must_find_papers(sample_id):
    s = replay.sample(sample_id)
    hits = []
    for q in s.plan["queries"]:
        response = search(s, q["query"])
        assert response["status"] == 200, q
        hits += response["json"]["items"]
    assert len(found(s.question, hits)) == FOUND[sample_id]
    asked = search(s, s.question["question"])
    assert asked["status"] != 200 or found(s.question, asked["json"]["items"]) == []


@pytest.mark.parametrize("sample_id", IDS)
def test_every_must_find_paper_with_a_doi_or_arxiv_id_has_a_lookup(sample_id):
    s = replay.sample(sample_id)
    for paper in s.question["must_find"]:
        for key in paper["keys"]:
            if (q := replay.lookup_id(key)) is not None:
                response = s.essaies.find("GET", "/api/openalex/lookup", [("q", q)])
                assert response["status"] == 200, key
                assert {"work", "cites", "cited_by"} <= set(response["json"]), key


def test_replays_through_the_isbe_http_client_and_refuses_unrecorded_requests():
    s = replay.sample("radar-nowcasting-dl")
    query = s.plan["queries"][0]["query"]
    with http.Http(5, transport=s.essaies.transport()) as client:
        response = client.get(
            "http://essaies.invalid/api/openalex/search", params={"q": query, "limit": replay.SEARCH_LIMIT}
        )
        assert response.status == 200
        assert response.json() == search(s, query)["json"]
        assert s.essaies.served == [replay.request_key("GET", "/api/openalex/search",
                                                       [("q", query), ("limit", replay.SEARCH_LIMIT)])]
        with pytest.raises(replay.Unrecorded, match="no recording for GET /api/openalex/search"):
            client.get("http://essaies.invalid/api/openalex/search", params={"q": "unrecorded", "limit": "100"})


def test_recorded_failures_replay_as_they_happened():
    # essaies passes an English question with its "?" to OpenAlex, which rejects it.
    s = replay.sample("neural-pde-solvers")
    with http.Http(5, transport=s.essaies.transport()) as client:
        response = client.get("http://essaies.invalid/api/openalex/search",
                              params={"q": s.question["question"], "limit": replay.SEARCH_LIMIT})
    assert response.status == 502


def test_no_fixture_file_leaks_internal_hosts_paths_or_secrets():
    files = sorted(p for p in replay.FIXTURES.rglob("*") if p.is_file())
    assert files
    for path in files:
        text = path.read_text()
        assert [k for k, p in replay.LEAK_PATTERNS.items() if p.search(text)] == [], path
        if path.suffix == ".json":
            assert replay.leaks(json.loads(text)) == [], path


@pytest.mark.parametrize(
    "value",
    [
        "http://10.20.30.40:8080/v1",
        "see 10.0.0.12",
        "http://localhost:3000",
        "/mnt/data/archive/x.pdf",
        "E:\\codes\\x",
        "Bearer abcdefghijklmnop",
        "sk-" + "a" * 24,
    ],
)
def test_leak_scan_catches(value):
    assert replay.leaks({"x": [value]})


def test_leak_scan_passes_paper_metadata():
    work = {"doi": "10.1038/s41586-021-03854-z", "url": "https://arxiv.org/pdf/1506.04214",
            "title": "Version 10.1.2 of a 3D model", "path": "/mnt/archive/a.pdf"}
    assert replay.leaks(replay.scrub(work)) == []


@pytest.mark.live
def test_live_essaies_still_returns_what_was_recorded():
    """`ISBE_LIVE_ESSAIES_URL=... pytest -m live`: drift between the recordings and essaies."""
    base = os.environ.get("ISBE_LIVE_ESSAIES_URL")
    if not base:
        pytest.skip("ISBE_LIVE_ESSAIES_URL is not set")
    with httpx.Client(base_url=base, timeout=120, trust_env=False) as client:
        for sample_id in IDS:
            s = replay.sample(sample_id)
            query = s.plan["queries"][0]["query"]
            live = client.get("/api/openalex/search", params={"q": query, "limit": replay.SEARCH_LIMIT}).json()
            assert found(s.question, live["items"]) == found(s.question, search(s, query)["json"]["items"]), sample_id
