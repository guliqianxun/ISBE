"""Recorded essaies traffic and a scripted model for the question-research tests,
so nothing needs a live essaies or Strata. The data is in tests/fixtures/question
(see its README); frontend tests read the same JSON files.

- `Cassette` holds recorded HTTP exchanges and replays them as an
  `httpx.MockTransport`: a request is matched on method, path and query (never
  the host, which is not recorded); an unrecorded request fails the test.
- `FakeModel` answers OpenAI-compatible chat completions with fixed replies,
  chosen by prompt hash or by text the prompt contains, and can inject the
  failures the pipeline must survive: broken JSON, a timeout, Strata's context
  window 400, a cut-off reply, a refusal or a server error.
- `ModelRecorder` passes chat completions through to a real model and keeps
  each reply under its prompt hash, for `FakeModel` to replay.
- `record` rebuilds the essaies cassettes from a live server:

      ISBE_LIVE_ESSAIES_URL=http://<essaies> uv run python tests/py/replay.py [--missing] [sample-id ...]

  `--missing` adds only what the cassettes lack (or recorded as a failure).
  `--arxiv` records instead the searches of the Strata plan written for essaies'
  arXiv index (`essaies.arxiv.json`); `--citations` the expand stage's citation
  lookups of the sample's candidates (`essaies.citations.json`).
- `uv run python tests/py/replay.py --events [sample-id ...]` rebuilds `events.json`,
  a whole run's event log the frontend replays (`run_events`), for the samples
  named or else those that have one.

Recorded bodies go through `scrub` and `leaks` before they are written.
"""

import copy
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "question"
CASSETTE = "cassette@1"
ARXIV_CASSETTE = "essaies.arxiv.json"
MODEL_REPLIES = "model_replies@1"
# One OpenAlex search returns at most this many works through essaies.
SEARCH_LIMIT = "100"

type Query = list[tuple[str, str]]


# --- recorded HTTP ---------------------------------------------------------------------


def request_key(method: str, path: str, query: Iterable[tuple[str, str]], body: Any = None) -> str:
    """`GET /api/openalex/search?limit=100&q=...`, with the query sorted, and a JSON
    request body (a batch lookup) after it."""
    key = f"{method.upper()} {path}?{httpx.QueryParams(sorted(query))}"
    return key if body is None else f"{key} {json.dumps(body, sort_keys=True, ensure_ascii=False)}"


def _request_body(request: httpx.Request) -> Any:
    return json.loads(request.content) if request.content else None


class Unrecorded(Exception):
    """A request the cassette has no recording for."""


@dataclass
class Cassette:
    """Recorded exchanges of one service, replayed in any order and any number of times."""

    service: str
    recorded_on: str = ""
    interactions: list[dict[str, Any]] = field(default_factory=list)
    # Request keys served so far, for tests that check what was asked.
    served: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Cassette:
        doc = json.loads(path.read_text())
        if doc.get("schema") != CASSETTE:
            raise ValueError(f"{path}: not a {CASSETTE} file")
        return cls(doc["service"], doc["recorded_on"], doc["interactions"])

    def save(self, path: Path) -> None:
        doc = {
            "schema": CASSETTE,
            "service": self.service,
            "recorded_on": self.recorded_on,
            "interactions": sorted(self.interactions, key=lambda i: _key(i["request"])),
        }
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")

    def add(self, method: str, path: str, query: Query, status: int, body: Any, sent: Any = None) -> None:
        request = {"method": method.upper(), "path": path, "query": [list(p) for p in sorted(query)]}
        if sent is not None:
            request["json"] = sent
        self.interactions = [i for i in self.interactions if _key(i["request"]) != _key(request)]
        self.interactions.append({"request": request, "response": {"status": status, "json": body}})

    def find(self, method: str, path: str, query: Iterable[tuple[str, str]], sent: Any = None) -> dict[str, Any]:
        key = request_key(method, path, query, sent)
        for interaction in self.interactions:
            if _key(interaction["request"]) == key:
                return interaction["response"]
        raise Unrecorded(f"{self.service}: no recording for {key}")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        sent = _request_body(request)
        response = self.find(request.method, request.url.path, request.url.params.multi_items(), sent)
        self.served.append(request_key(request.method, request.url.path, request.url.params.multi_items(), sent))
        return httpx.Response(response["status"], json=response["json"])

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def _key(request: dict[str, Any]) -> str:
    return request_key(request["method"], request["path"], [tuple(p) for p in request["query"]], request.get("json"))


@dataclass
class EssaiesRecorder:
    """A transport that passes requests to a live essaies and adds each exchange
    (scrubbed, refused if it leaks) to `cassette`; with `missing`, a request the
    cassette already holds a 200 for is answered from it instead."""

    upstream: httpx.BaseTransport
    cassette: Cassette
    today: str
    missing: bool = False
    label: str = ""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path, query, sent = request.url.path, request.url.params.multi_items(), _request_body(request)
        if self.missing:
            try:
                kept = self.cassette.find(request.method, path, query, sent)
            except Unrecorded:
                pass
            else:
                if kept["status"] == 200:
                    return httpx.Response(200, json=kept["json"])
        response = self.upstream.handle_request(request)
        response.read()
        body = scrub(response.json())
        found = leaks(body)
        if found:
            raise SystemExit(f"{self.label} {request_key(request.method, path, query, sent)}: {found[:3]}")
        self.cassette.add(request.method, path, query, response.status_code, body, sent)
        self.cassette.interactions[-1]["recorded_on"] = self.today
        print(f"{self.label}: {response.status_code} {request_key(request.method, path, query)}", file=sys.stderr)
        return httpx.Response(response.status_code, json=body)

    def transport(self) -> httpx.BaseTransport:
        recorder = self

        class Transport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                return recorder.handle_request(request)

        return Transport()


@dataclass(frozen=True)
class Sample:
    """One sample question with its recorded data."""

    id: str
    question: dict[str, Any]  # the entry in samples.json
    plan: dict[str, Any]  # question_plan@1, the plan the fake model returns
    essaies: Cassette

    @property
    def dir(self) -> Path:
        return FIXTURES / self.id

    @property
    def arxiv(self) -> Cassette | None:
        """The recorded searches of essaies' arXiv index (essaies.arxiv.json), if any."""
        path = self.dir / ARXIV_CASSETTE
        return Cassette.load(path) if path.is_file() else None

    def model(self, name: str = "model.json") -> FakeModel:
        """A fake model loaded with this sample's replies (model.json, or the recorded
        Strata replies in model.strata.json)."""
        return FakeModel.load(self.dir / name)

    def strata_plan(self, engine: str = "openalex") -> dict[str, Any] | None:
        """The plan ISBE makes from Strata's recorded reply (model.strata.json), its
        queries written for `engine`, or None when the sample has none."""
        if not (self.dir / "model.strata.json").is_file():
            return None
        from isbe.core import db, http
        from isbe.core.llm import Llm
        from isbe.question import compose, plan

        env = {
            "ISBE_LLM_PROVIDER": "openai",
            "ISBE_LLM_BASE_URL": "http://127.0.0.1:9/v1",  # never reached: the transport answers
            "ISBE_LLM_NO_AUTH": "true",
            "ISBE_LLM_MODEL_SMART": "strata",
        }
        llm = Llm.from_vars(env.get, client=http.Http(5, transport=self.model("model.strata.json").transport()))
        q = self.question
        return plan.generate(
            db.connect(":memory:"), llm, f"run-sample-{q['id']}", q["question"], q["lang"],
            engine=compose.ENGINES[engine],
        )

    def strata_recorded(self, engine: str = "openalex") -> bool:
        """Whether the cassette of `engine` holds every search the Strata plan's
        queries make (false after a prompt change until `replay.py --missing`, or
        `--arxiv`, records them)."""
        plan = self.strata_plan(engine)
        cassette = self.essaies if engine == "openalex" else self.arxiv
        if plan is None or cassette is None:
            return False
        probe = Cassette(cassette.service, cassette.recorded_on, cassette.interactions)
        try:
            client_run(self, "http://essaies.test", probe.transport(), [plan], engine)
        except Unrecorded:
            return False
        return True


def sample_ids() -> list[str]:
    return sorted(p.name for p in FIXTURES.iterdir() if (p / "plan.json").is_file())


def sample(sample_id: str) -> Sample:
    from question_schemas import samples

    question = next(q for q in samples() if q["id"] == sample_id)
    d = FIXTURES / sample_id
    return Sample(
        sample_id,
        question,
        json.loads((d / "plan.json").read_text()),
        Cassette.load(d / "essaies.json"),
    )


def lookup_id(key: str) -> str | None:
    """What essaies' `/api/openalex/lookup` takes for an identity key: a DOI or an arXiv id."""
    kind, _, value = key.partition(":")
    return value if kind in ("doi", "arxiv") else None


# --- the fake model --------------------------------------------------------------------


def prompt_sha(system: str, user: str) -> str:
    """The key of a recorded reply: SHA-256 over the system and user text only, so a
    retry with a lowered max_tokens finds the same reply."""
    return hashlib.sha256(json.dumps([system, user], ensure_ascii=False).encode()).hexdigest()


def _prompt(body: dict[str, Any]) -> tuple[str, str]:
    messages = body.get("messages") or []
    system = "".join(m.get("content", "") for m in messages if m.get("role") == "system")
    user = "".join(m.get("content", "") for m in messages if m.get("role") == "user")
    return system, user


FAULTS = ("bad_json", "timeout", "context", "cut", "refuse", "server_error")

# Strata's rejection when prompt + max_tokens exceed the context window.
CONTEXT_MESSAGE = (
    "request exceeds the context window: prompt ({prompt} tokens) + max tokens ({max_tokens}) "
    "exceeds the context ({context})"
)


@dataclass
class _Rule:
    sha: str | None
    contains: str | None
    reply: str | None = None
    fault: str | None = None
    times: int | None = None  # None: every matching call

    def matches(self, sha: str, system: str, user: str) -> bool:
        if self.times == 0:
            return False
        if self.sha is not None:
            return self.sha == sha
        return self.contains is None or self.contains in system or self.contains in user


@dataclass
class FakeModel:
    """An OpenAI-compatible chat endpoint for `httpx.MockTransport`.

    Faults are checked before replies, newest first; replies in the order added.
    A call nothing matches fails with `Unrecorded`, naming the prompt hash.
    """

    context: int = 262144
    calls: list[dict[str, Any]] = field(default_factory=list)
    _replies: list[_Rule] = field(default_factory=list)
    _faults: list[_Rule] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> FakeModel:
        doc = json.loads(path.read_text())
        if doc.get("schema") != MODEL_REPLIES:
            raise ValueError(f"{path}: not a {MODEL_REPLIES} file")
        model = cls()
        for entry in doc["replies"]:
            model.reply(entry["reply"], sha=entry.get("prompt_sha"), contains=entry.get("contains"))
        return model

    def reply(self, text: str | Any, *, sha: str | None = None, contains: str | None = None) -> FakeModel:
        """Answer matching prompts with `text` (a non-string is sent as its JSON)."""
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False)
        self._replies.append(_Rule(sha, contains, reply=text))
        return self

    def fault(
        self, kind: str, *, sha: str | None = None, contains: str | None = None, times: int | None = 1
    ) -> FakeModel:
        """Fail the next `times` matching calls (None: all of them) with `kind`, one of FAULTS."""
        if kind not in FAULTS:
            raise ValueError(f"unknown fault {kind!r}; expected one of {FAULTS}")
        self._faults.insert(0, _Rule(sha, contains, fault=kind, times=times))
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system, user = _prompt(body)
        sha = prompt_sha(system, user)
        max_tokens = body.get("max_completion_tokens") or body.get("max_tokens") or 0
        self.calls.append({"sha": sha, "system": system, "user": user, "max_tokens": max_tokens})
        for rule in self._faults:
            if rule.matches(sha, system, user):
                if rule.times is not None:
                    rule.times -= 1
                return self._fail(rule.fault or "", request, system + user, max_tokens)
        for rule in self._replies:
            if rule.matches(sha, system, user):
                return _completion(rule.reply or "", "stop", system + user)
        raise Unrecorded(f"model: no reply for prompt {sha} ({user[:80]!r})")

    def _fail(self, kind: str, request: httpx.Request, prompt: str, max_tokens: int) -> httpx.Response:
        match kind:
            case "timeout":
                raise httpx.ReadTimeout("fake model timed out", request=request)
            case "bad_json":
                return _completion('{"queries": [{"query": "unterminated', "stop", prompt)
            case "cut":
                return _completion('{"queries": [', "length", prompt)
            case "refuse":
                return _completion("", "content_filter", prompt)
            case "server_error":
                return httpx.Response(500, json={"error": {"message": "internal error"}})
            case _:  # context
                tokens = _tokens(prompt)
                message = CONTEXT_MESSAGE.format(prompt=tokens, max_tokens=max_tokens, context=self.context)
                return httpx.Response(
                    400, json={"error": {"type": "invalid_request_error", "message": message}}
                )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _completion(text: str, finish: str, prompt: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "model": "fake",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}],
            "usage": {"prompt_tokens": _tokens(prompt), "completion_tokens": _tokens(text)},
        },
    )


@dataclass
class ModelRecorder:
    """Pass chat completions to `upstream` and keep each 200 reply by prompt hash."""

    upstream: httpx.BaseTransport
    purpose: Callable[[str, str], str] = lambda system, user: ""
    replies: list[dict[str, Any]] = field(default_factory=list)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.upstream.handle_request(request)
        response.read()
        if response.status_code == 200:
            system, user = _prompt(json.loads(request.content))
            content = response.json()["choices"][0]["message"]["content"]
            sha = prompt_sha(system, user)
            self.replies = [r for r in self.replies if r["prompt_sha"] != sha]
            self.replies.append({"purpose": self.purpose(system, user), "prompt_sha": sha, "reply": content})
        return httpx.Response(response.status_code, headers=response.headers, content=response.content)

    def transport(self) -> httpx.BaseTransport:
        recorder = self

        class Transport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                return recorder.handle_request(request)

        return Transport()

    def save(self, path: Path, *, merge: bool = True) -> None:
        doc = json.loads(path.read_text()) if merge and path.exists() else {"schema": MODEL_REPLIES, "replies": []}
        shas = {r["prompt_sha"] for r in self.replies}
        kept = [r for r in doc["replies"] if r.get("prompt_sha") not in shas]
        replies = [scrub(r) for r in self.replies]
        found = [hit for r in replies for hit in leaks(r)]
        if found:
            raise ValueError(f"refusing to save model replies with {found[:3]}")
        doc["replies"] = kept + replies
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")


# --- leak control ----------------------------------------------------------------------

# Fields essaies adds that describe its own host, never the paper.
_HOST_FIELDS = frozenset({"path"})

LEAK_PATTERNS = {
    "private address": re.compile(
        r"(?<![\d.])(?:10(?:\.\d{1,3}){3}|127(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}"
        r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?![\d.])"
    ),
    "localhost": re.compile(r"\blocalhost\b", re.I),
    "local path": re.compile(r"(?:^|[\s\"'(=])(?:/mnt/|/home/|/Users/|/srv/|/var/|[A-Za-z]:\\)"),
    "key": re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|glpat-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._-]{12,})"),
}


def scrub(value: Any) -> Any:
    """A copy without the fields that describe the server rather than the paper."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k not in _HOST_FIELDS}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return copy.copy(value)


def leaks(value: Any, where: str = "$") -> list[str]:
    """`<json path>: <kind>` for every string that looks like an internal host, a
    local path or a secret."""
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            found += leaks(k, f"{where}.{k}(key)") + leaks(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            found += leaks(v, f"{where}[{i}]")
    elif isinstance(value, str):
        found += [f"{where}: {kind}" for kind, pattern in LEAK_PATTERNS.items() if pattern.search(value)]
    return found


# --- recording -------------------------------------------------------------------------


def essaies_requests(question: dict[str, Any], plan: dict[str, Any]) -> list[tuple[str, Query]]:
    """One plain search per plan query and for the question as asked (limit 100, no
    other parameter), and a lookup of every must-find paper's DOI and arXiv id: what
    `test_recorded_samples` checks, whatever the client asks."""
    searches = [plan["question"], *(q["query"] for q in sorted(plan["queries"], key=lambda q: q["priority"]))]
    out: list[tuple[str, Query]] = [
        ("/api/openalex/search", [("limit", SEARCH_LIMIT), ("q", q)]) for q in dict.fromkeys(searches)
    ]
    ids = [i for paper in question["must_find"] for key in paper["keys"] if (i := lookup_id(key))]
    out += [("/api/openalex/lookup", [("q", i)]) for i in dict.fromkeys(ids)]
    return out


def record(base: str, ids: list[str], today: str, *, missing: bool = False) -> None:
    """Record each sample's cassette again: the requests of `essaies_requests` as one
    plain search or lookup each, then everything ISBE's own client asks while it
    searches with `plan.json` and the plan Strata's recorded reply gives and looks
    up the must-find papers. With `missing`, the plain requests are kept as they are
    and the client's are added where the cassette has no 200 for them, marked with
    today's date."""
    with httpx.Client(base_url=base.rstrip("/"), timeout=120, trust_env=False) as client:
        for sample_id in ids:
            s = sample(sample_id)
            d = s.dir
            plan = s.plan
            strata = s.strata_plan()
            cassette = s.essaies if missing else Cassette("essaies", today)
            for path, query in [] if missing else essaies_requests(s.question, plan):
                response = client.get(path, params=query)
                body = scrub(response.json())
                found = leaks(body)
                if found:
                    raise SystemExit(f"{sample_id} {path} {query}: {found[:3]}")
                cassette.add("GET", path, query, response.status_code, body)
                print(f"{sample_id}: {response.status_code} {request_key('GET', path, query)}", file=sys.stderr)
            recorder = EssaiesRecorder(httpx.HTTPTransport(), cassette, today, missing, sample_id)
            client_run(s, base, recorder.transport(), [plan, *([strata] if strata else [])])
            cassette.save(d / "essaies.json")
            model = d / "model.json"
            if not model.exists():
                # The plan as the planning call's reply, until a real model is recorded.
                doc = {"schema": MODEL_REPLIES, "replies": [
                    {"purpose": "plan", "contains": plan["question"], "reply": plan_reply(plan)}
                ]}
                model.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")


def record_arxiv(base: str, ids: list[str], today: str) -> None:
    """Record each sample's arXiv-index cassette again: every search of the Strata
    plan written for the arXiv index."""
    for sample_id in ids:
        s = sample(sample_id)
        strata = s.strata_plan("arxiv")
        if strata is None:
            continue
        cassette = Cassette("essaies", today)
        recorder = EssaiesRecorder(httpx.HTTPTransport(), cassette, today, False, sample_id)
        client_run(s, base, recorder.transport(), [strata], "arxiv")
        # arXiv's titles and abstracts keep their line breaks, which the file-wide
        # leak check would read as a drive path ("Part I:\\n"); ISBE squashes them anyway.
        for interaction in cassette.interactions:
            interaction["response"]["json"] = _squash_strings(interaction["response"]["json"])
        cassette.save(s.dir / ARXIV_CASSETTE)


CITATIONS_CASSETTE = "essaies.citations.json"
# Kept candidates whose full lookups are recorded (each costs OpenAlex quota).
CITATIONS_FULL = 20


def record_citations(base: str, ids: list[str], today: str) -> None:
    """Record each sample's citation lookups again (`essaies.citations.json`): the
    run of `tests/py/question_flow.py` on the sample with every paper kept
    (`KEEP_ALL`), then the expand stage's archive batches for its candidates and
    the full lookups of its `CITATIONS_FULL` most cited ones, as ISBE's own client asks them. A full
    lookup OpenAlex refused (out of quota) is recorded as it was answered."""
    import tempfile
    from contextlib import closing

    import question_flow as qf

    from isbe.core import db, http, paths
    from isbe.question import citations, service, sources

    for sample_id in ids:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            svc = qf.services(qf.Model(sample_id, keep=qf.KEEP_ALL), qf.Essaies(sample_id))
            with closing(db.connect(paths.workspace(root, "local") / "isbe.db")) as conn:
                question = sample(sample_id).question
                run, _ = service.ask(root, conn, "local", {"question": question["question"], "lang": question["lang"],
                                                           "engine": "openalex", "review": False, "expand": False,
                                                           "citations_full": 0})
            qf.drain(root, svc)
            cassette = Cassette("essaies", today)
            recorder = EssaiesRecorder(httpx.HTTPTransport(), cassette, today, False, sample_id)
            source = sources.Essaies(http.Http(sources.TIMEOUT, transport=recorder.transport()), base)
            with closing(db.connect(paths.workspace(root, "local") / "isbe.db")) as conn:
                conn.execute("DELETE FROM question_citations WHERE run_id = ?", (run["run_id"],))
                citations.run(conn, source, run["run_id"], full_limit=CITATIONS_FULL, events=False)
            for interaction in cassette.interactions:
                interaction["response"]["json"] = _squash_strings(interaction["response"]["json"])
            cassette.save(sample(sample_id).dir / CITATIONS_CASSETTE)


def _squash_strings(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _squash_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_squash_strings(v) for v in value]
    return " ".join(value.split()) if isinstance(value, str) else value


def client_run(
    s: Sample, base: str, transport: httpx.BaseTransport, plans: list[dict[str, Any]], engine: str = "openalex"
) -> None:
    """Search every plan with ISBE's search stage on `engine` (every query, no early
    stop) and, on OpenAlex, look up the must-find papers, through `transport`."""
    from isbe.core import db, http, jsonfmt
    from isbe.question import runs, search, sources

    source = sources.Essaies(http.Http(sources.TIMEOUT, transport=transport), base, engine=engine)
    for plan in plans:
        conn = db.connect(":memory:")
        run_id = runs.create(conn, plan["question"], plan["lang"])
        saved = {**plan, "run_id": run_id, "version": 1, "parent_version": None}
        conn.execute(
            "INSERT INTO question_plans (run_id, version, source, plan, created_at) VALUES (?, 1, ?, ?, ?)",
            (run_id, saved["source"], jsonfmt.dumps(saved, sort_keys=False), saved["created_at"]),
        )
        search.run(conn, source, run_id, enough=10**9)
    if engine == "arxiv":
        return
    source.lookup_many(i for p in s.question["must_find"] for key in p["keys"] if (i := lookup_id(key)))


def screening_plan(s: Sample) -> dict[str, Any]:
    """The plan the screening tests use: the queries of `plan.json` and of Strata's
    plan (repeats once), each keeping its priority, so the candidates are every
    recorded search's papers (~1,000 a sample) and the narrow queries are known.
    Criteria, restatement and years are `plan.json`'s. A sample with
    `screen.queries.json` uses the queries pinned there instead: the ones its
    recorded screening (`model.screen.json`) ran on, before the Strata plan's
    queries were composed per engine (#47)."""
    plan = copy.deepcopy(s.plan)
    pinned = s.dir / "screen.queries.json"
    if pinned.is_file():
        # The queries model.screen.json was recorded on: the Strata plan has changed since.
        plan["queries"] = [{**q, "layer": "canonical"} for q in json.loads(pinned.read_text())["queries"]]
        return plan
    strata = s.strata_plan()
    queries, seen = [], set()
    for q in [*plan["queries"], *(strata["queries"] if strata else [])]:
        if q["query"].casefold() in seen:
            continue
        seen.add(q["query"].casefold())
        queries.append({**q, "id": f"q{len(queries) + 1}"})
    plan["queries"] = queries
    return plan


def screening_run(s: Sample) -> tuple[Any, str, str]:
    """A database with a run of `screening_plan(s)` whose candidate set is built from
    the recorded essaies 0.5 replies to its queries (each with the years of the plan
    it came from, so not one search): the connection, the run id and the candidate
    set's id (`s1.candidates`). The replies come from `essaies.screen.json` when the
    sample has it (the searches of the pinned queries, kept from the cassette they
    were recorded in), else from `essaies.json`."""
    from isbe.core import db, jsonfmt
    from isbe.question import runs, sets, sources

    plan = screening_plan(s)
    conn = db.connect(":memory:")
    run_id = runs.create(conn, plan["question"], plan["lang"], run_id=f"run-screen-{s.id}")
    saved = {**plan, "run_id": run_id, "version": 1, "parent_version": None}
    conn.execute(
        "INSERT INTO question_plans (run_id, version, source, plan, created_at) VALUES (?, 1, ?, ?, ?)",
        (run_id, saved["source"], jsonfmt.dumps(saved, sort_keys=False), saved["created_at"]),
    )
    replies: dict[str, dict[str, Any]] = {}
    pinned = s.dir / "essaies.screen.json"
    cassette = Cassette.load(pinned) if pinned.is_file() else s.essaies
    for i in cassette.interactions:
        request, response = i["request"], i["response"]
        query = dict(request["query"])
        if request["path"] == "/api/openalex/search" and "include" in query and response["status"] == 200:
            replies.setdefault(query["q"], response["json"])
    store = sets.Store(conn, run_id)
    ids = []
    for q in plan["queries"]:
        body = replies.get(q["query"])
        if body is None:
            continue
        members = []
        for rank, work in enumerate(body.get("items") or [], 1):
            paper = sources.paper(work)
            if paper is not None:
                members.append({"paper": paper, "provenance": [{"via": "search", "query_id": q["id"], "rank": rank}]})
        ids.append(store.add(sets.PaperSet(f"s1.{q['id']}", "query", q["query"], sets.union([members]))).set_id)
    store.run("union", ids, "s1.union", "candidates", "All query results", {"plan_version": 1})
    store.run("dedup", ["s1.union"], "s1.candidates", "candidates", "Candidates after deduplication")
    return conn, run_id, "s1.candidates"


EVENTS = "events.json"
# Recorded logs are timed from here, one event every 40 ms.
EVENTS_START = "2026-10-23T10:00:00"


def run_events(s: Sample) -> list[dict[str, Any]]:
    """A whole run of `screening_plan(s)` (its recorded queries, with the concepts of
    Strata's plan) as `question_event@1` events, for the frontend to replay: its
    states, stages and plan, a hit per recorded search
    result (in query order, `new` as the search stage sets it), the dedup folds,
    Strata's recorded screening (`model.screen.json`; `scripted_screener` judges the
    batches it has no reply for), then expansion and graph
    skipped (not built yet) and `done`. Times are synthetic, so the log is the same
    on every rebuild."""
    from datetime import datetime, timedelta

    from isbe.core import db, http, jsonfmt
    from isbe.core.llm import Llm
    from isbe.question import runs, screen, sets, sources

    replies: dict[str, dict[str, Any]] = {}
    pinned = s.dir / "essaies.screen.json"
    for i in (Cassette.load(pinned) if pinned.is_file() else s.essaies).interactions:
        request, response = i["request"], i["response"]
        query = dict(request["query"])
        if request["path"] == "/api/openalex/search" and "include" in query and response["status"] == 200:
            replies.setdefault(query["q"], response["json"])
    plan = screening_plan(s)
    plan["queries"] = [q for q in plan["queries"] if q["query"] in replies]
    strata = s.strata_plan()
    if strata and strata.get("concepts"):
        plan["concepts"] = strata["concepts"]
    conn = db.connect(":memory:")
    run_id = runs.create(conn, plan["question"], plan["lang"], run_id=f"run-screen-{s.id}")
    saved = {**plan, "run_id": run_id, "version": 1, "parent_version": None}
    conn.execute(
        "INSERT INTO question_plans (run_id, version, source, plan, created_at) VALUES (?, 1, ?, ?, ?)",
        (run_id, saved["source"], jsonfmt.dumps(saved, sort_keys=False), saved["created_at"]),
    )
    runs.emit(conn, run_id, [
        ("state", {"state": "running"}), ("stage", {"stage": "plan", "state": "begin"}),
        ("plan", {"plan": saved}), ("stage", {"stage": "plan", "state": "end"}),
        ("stage", {"stage": "search", "state": "begin"}),
    ])
    store = sets.Store(conn, run_id)
    ids, seen = [], set()
    for q in plan["queries"]:
        body = replies[q["query"]]
        members = []
        for rank, work in enumerate(body.get("items") or [], 1):
            paper = sources.paper(work)
            if paper is not None:
                members.append({"paper": paper, "provenance": [{"via": "search", "query_id": q["id"], "rank": rank}]})
        query_set = sets.union([members])
        hits = []
        for m in query_set:
            key = m["paper"]["key"]
            hits.append(("hit", {"query_id": q["id"], "rank": m["provenance"][0]["rank"],
                                 "paper": m["paper"], "new": key not in seen}))
            seen.add(key)
        runs.emit(conn, run_id, hits)
        ids.append(store.add(sets.PaperSet(f"s1.{q['id']}", "query", q["query"], query_set)).set_id)
    store.run("union", ids, "s1.union", "candidates", "All query results", {"plan_version": 1})
    _, _, folds = store.run("dedup", ["s1.union"], "s1.candidates", "candidates", "Candidates after deduplication")
    runs.emit(conn, run_id, [("dedup", {"kept": kept, "merged": merged}) for kept, merged in folds]
              + [("stage", {"stage": "search", "state": "end"})])

    recorded = s.model("model.screen.json")
    terms = [t for c in plan.get("concepts") or [] if c["role"] == "subject" for t in c["terms"]]
    scripted = scripted_screener(terms or plan["subject_terms"])

    def answer(request: httpx.Request) -> httpx.Response:
        try:
            return recorded(request)
        except Unrecorded:
            return scripted(request)

    env = {
        "ISBE_LLM_PROVIDER": "openai",
        "ISBE_LLM_BASE_URL": "http://127.0.0.1:9/v1",  # never reached: the transport answers
        "ISBE_LLM_NO_AUTH": "true",
        "ISBE_LLM_MODEL_FAST": "replay",
    }
    llm = Llm.from_vars(env.get, client=http.Http(5, transport=httpx.MockTransport(answer)))
    llm._sleep = lambda _: None
    result = screen.run(conn, llm, run_id, "s1.candidates")
    status = "partial" if result.partial else "completed"
    runs.emit(conn, run_id, [
        ("stage", {"stage": "expand", "state": "skipped"}), ("stage", {"stage": "graph", "state": "skipped"}),
        ("state", {"state": "done"}), ("done", {"status": status}),
    ])
    start = datetime.fromisoformat(EVENTS_START)
    out = runs.events(conn, run_id)
    for e in out:
        e["at"] = (start + timedelta(milliseconds=40 * e["seq"])).isoformat(timespec="milliseconds") + "Z"
        if e["type"] == "verdict":
            e["data"]["verdict"]["created_at"] = e["at"]
    return out


_PAPER = re.compile(r"^\[(\d+)\] (.*?)(?=^\[\d+\] |\Z)", re.M | re.S)


def scripted_screener(terms: list[str]) -> Callable[[httpx.Request], httpx.Response]:
    """A stand-in for the screening model where no reply is recorded: it includes a
    paper whose title or abstract names one of `terms` and excludes the rest, and
    says so in every reason, so a replayed log never passes it off as Strata's."""
    wanted = [t.casefold() for t in terms]

    def handle(request: httpx.Request) -> httpx.Response:
        _, user = _prompt(json.loads(request.content))
        verdicts = []
        for n, text in _PAPER.findall(user):
            found = next((t for t in wanted if t in text.casefold()), None)
            verdicts.append({
                "n": int(n), "decision": "include" if found else "exclude", "score": 0.8 if found else 0.15,
                "reason": f"Scripted verdict: mentions “{found}”." if found else "Scripted verdict: names no subject term.",
            })
        return _completion(json.dumps({"verdicts": verdicts}), "stop", user)

    return handle


def write_events(sample_id: str) -> Path:
    """Rebuild `<sample>/events.json` from the recordings."""
    path = FIXTURES / sample_id / EVENTS
    path.write_text(json.dumps(run_events(sample(sample_id)), ensure_ascii=False, separators=(",", ":")) + "\n")
    return path


def plan_reply(plan: dict[str, Any]) -> str:
    """The model's part of a plan: what is left after the fields ISBE fills in itself."""
    own = ("schema", "run_id", "version", "parent_version", "created_at", "source", "question", "lang", "llm")
    return json.dumps({k: v for k, v in plan.items() if k not in own}, ensure_ascii=False)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    if "--events" in sys.argv:
        chosen = [a for a in sys.argv[1:] if not a.startswith("--")]
        for sample_id in chosen or [i for i in sample_ids() if (FIXTURES / i / EVENTS).is_file()]:
            print(write_events(sample_id))
        raise SystemExit
    base = os.environ.get("ISBE_LIVE_ESSAIES_URL")
    if not base:
        raise SystemExit("set ISBE_LIVE_ESSAIES_URL to the essaies base URL")
    from datetime import date

    args = sys.argv[1:]
    ids = [a for a in args if not a.startswith("--")] or sample_ids()
    if "--arxiv" in args:
        record_arxiv(base, ids, date.today().isoformat())
    elif "--citations" in args:
        record_citations(base, ids, date.today().isoformat())
    else:
        record(base, ids, date.today().isoformat(), missing="--missing" in args)
