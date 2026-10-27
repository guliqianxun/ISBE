"""Question runs end to end without the network, shared by the flow, CLI and API
tests: a sample's recorded plan reply and essaies searches (`replay`), and a
scripted screening model."""

import json
import re
from collections.abc import Callable
from pathlib import Path

import httpx
import replay

from isbe.core import http
from isbe.core.llm import Llm
from isbe.question import flow, sources

SAMPLE = "neural-pde-solvers"
PAPER = re.compile(r"^\[(\d+)\] (.*)$", re.M)


# A screening that keeps every paper: the seeds of the recorded citation runs
# (`replay.py --citations`), the same for every sample, as recall-first as an
# unscreened run.
KEEP_ALL = r"."


class Model:
    """The sample's recorded plan reply for planning calls; for screening calls, include
    the papers whose title matches `keep`. `on_screen(n)` runs before the nth
    screening call is answered (to pause or cancel mid-stage)."""

    def __init__(self, sample_id: str = SAMPLE, keep: str = r"neural|network|learning|operator|physics") -> None:
        self.plans = replay.FakeModel.load(replay.FIXTURES / sample_id / "model.json")
        self.keep = re.compile(keep, re.I)
        self.screened: list[str] = []  # titles judged, in order
        self.calls = 0  # screening calls
        self.on_screen: Callable[[int], None] = lambda n: None
        self.down = False  # every call fails as unreachable
        self.status: int | None = None  # every call answers this status
        self.requests = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.down:
            raise httpx.ConnectError("[Errno 111] Connection refused", request=request)
        if self.status is not None:
            return httpx.Response(self.status, json={"error": {"message": "invalid api key"}})
        body = json.loads(request.content)
        user = body["messages"][-1]["content"]
        if "Papers:" not in user:
            return self.plans(request)
        self.calls += 1
        self.on_screen(self.calls)
        titles = PAPER.findall(user)
        self.screened += [t for _, t in titles]
        verdicts = [
            {"n": int(n), "decision": "include" if self.keep.search(t) else "exclude",
             "score": 0.8 if self.keep.search(t) else 0.1, "reason": "scripted"}
            for n, t in titles
        ]
        return replay._completion(json.dumps({"verdicts": verdicts}), "stop", user)

    def llm(self) -> Llm:
        env = {
            "ISBE_LLM_PROVIDER": "openai",
            "ISBE_LLM_BASE_URL": "http://127.0.0.1:9/v1",  # never reached: the transport answers
            "ISBE_LLM_NO_AUTH": "true",
            "ISBE_LLM_MODEL": "fake",
            "ISBE_LLM_MODEL_FAST": "fake",
        }
        llm = Llm.from_vars(env.get, client=http.Http(5, transport=httpx.MockTransport(self)))
        llm._sleep = lambda _: None
        return llm


class Essaies:
    """The sample's recorded essaies; `before(query)` runs before each search is
    answered, and `fail` makes every search fail.

    Citation lookups (batch, and lookups with a scope) are answered from the
    recorded ones with `recorded_citations`, else from `archive` and `full`, lookup id → (ids it cites, ids citing it); an id in neither is
    unknown. `quota` answers every full lookup 503, as essaies does when OpenAlex is
    out of quota; `failing` answers the full lookups of those ids with that status;
    `down` fails every full lookup as unreachable; `looked_up` lists the full
    lookups asked (in no fixed order: they go several at a time). `gone` makes
    every request fail as unreachable, `status` answers every request with it;
    `requests` counts them all."""

    def __init__(self, sample_id: str = SAMPLE, *, recorded_citations: bool = False) -> None:
        self.cassette = replay.sample(sample_id).essaies
        # The sample's recorded citation lookups (`replay.py --citations`) instead of
        # `archive` and `full`.
        self.citations = (replay.Cassette.load(replay.FIXTURES / sample_id / replay.CITATIONS_CASSETTE)
                          if recorded_citations else None)
        self.searched: list[str] = []
        self.before: Callable[[str], None] = lambda q: None
        self.fail = False
        self.archive: dict[str, tuple[list[str], list[str]]] = {}
        self.full: dict[str, tuple[list[str], list[str]]] = {}
        self.quota = False
        self.failing: dict[str, int] = {}
        self.down = False
        self.looked_up: list[str] = []
        self.batches = 0  # batch lookups answered
        self.on_batch: Callable[[int], None] = lambda n: None  # before the nth is answered
        self.gone = False
        self.status: int | None = None
        # The items of a search the sample did not record (a fallback plan's), else it fails.
        self.unrecorded: list[dict] | None = None
        self.requests = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.gone:
            raise httpx.ConnectError("[Errno 113] No route to host", request=request)
        if self.status is not None:
            return httpx.Response(self.status, json={"detail": "upstream timed out"})
        path, params = request.url.path, request.url.params
        if self.citations is not None and (path == sources.BATCH or (path == sources.LOOKUP and "scope" in params)):
            if path == sources.LOOKUP:
                self.looked_up.append(params["q"])
            return self.citations(request)
        if path == sources.BATCH:
            self.batches += 1
            self.on_batch(self.batches)
            ids = json.loads(request.content)["ids"]
            items = [{"query": i, **citing(i, self.archive[i])} for i in ids if i in self.archive]
            return httpx.Response(200, json={"items": items, "missing": [i for i in ids if i not in self.archive]})
        if path == sources.LOOKUP and "scope" in params:
            ident = params["q"]
            self.looked_up.append(ident)
            if self.down:
                raise httpx.ConnectError("connection refused", request=request)
            if self.quota:
                return httpx.Response(503, json={"detail": "OpenAlex rate, quota or plan limit reached; retry later"})
            if ident in self.failing:
                return httpx.Response(self.failing[ident], json={"detail": "upstream timed out"})
            if ident not in self.full:
                return httpx.Response(200, json={"work": None, "error": None})
            return httpx.Response(200, json=citing(ident, self.full[ident]))
        if path == sources.SEARCH and params.get("cursor") in (None, "*"):
            query = request.url.params.get("q", "")
            self.searched.append(query)
            self.before(query)
        if self.fail and request.url.path == sources.SEARCH:
            return httpx.Response(400, json={"error": "bad query"})
        if self.unrecorded is not None and path in (sources.SEARCH, sources.ARXIV_SEARCH):
            try:
                return self.cassette(request)
            except replay.Unrecorded:
                return httpx.Response(200, json={"items": self.unrecorded})
        return self.cassette(request)

    def source(self, engine: str = "openalex") -> sources.Essaies:
        return sources.Essaies(
            http.Http(5, transport=httpx.MockTransport(self)), "http://essaies.test", engine=engine,
            interval=0, retries=0, sleep=lambda _: None,
        )


def work(ident: str) -> dict:
    """A minimal essaies work for a lookup id (an arXiv id, else a DOI)."""
    arxiv = sources.arxiv_key(ident) is not None
    return {"id": ident, "arxiv_id": ident if arxiv else None, "doi": None if arxiv else ident, "title": f"Paper {ident}"}


def citing(ident: str, links: tuple[list[str], list[str]]) -> dict:
    """essaies' lookup record of `ident` citing `links[0]` and cited by `links[1]`."""
    return {"work": work(ident), "cites": [work(i) for i in links[0]], "cited_by": [work(i) for i in links[1]]}


def services(model: Model | None = None, essaies: Essaies | None = None, **kwargs) -> flow.Services:
    model = model or Model()
    essaies = essaies or Essaies()
    llm = model.llm()
    return flow.Services(model=lambda: (llm, ""), source=essaies.source, **kwargs)


def drain(root: Path, svc: flow.Services, limit: int = 20) -> int:
    """Take ready steps until none is left; how many were taken."""
    steps = 0
    while flow.work_one(root, svc):
        steps += 1
        assert steps < limit, "the run does not end"
    return steps
