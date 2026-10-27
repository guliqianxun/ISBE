"""The question routes, `/api/question/*`: asking, reading a run and its plan, papers,
sets and CSV, the actions, overrides, workspace isolation in shared mode, the JSON
event pages and the server-sent event stream (resumed with `Last-Event-ID`)."""

import gzip
import json
import socket
import time
from contextlib import closing
from pathlib import Path

import question_flow as qf
from conftest import TOKEN, add_user, call, login, make_console
from question_schemas import errors

from isbe.core import db, paths
from isbe.question import runs
from isbe.web.app import Endpoint
from isbe.web.wire import Request

PREFIX = "/api/question/runs"
QUESTION = {"question": "Which neural operators solve PDEs best?", "lang": "en"}


def finished_run(console, data: Path) -> str:
    """A run asked through the API and taken to the end by the flow."""
    made = call(console, "POST", PREFIX, {**qf_question(), "review": False})
    assert made.status == 202, made.body
    qf.drain(data, qf.services())
    return made.body["run"]["run_id"]


def qf_question() -> dict:
    return {"question": qf.replay.sample(qf.SAMPLE).question["question"], "lang": "en", "engine": "openalex"}


def test_ask_read_and_act_on_a_run(data: Path) -> None:
    console = make_console(data)
    made = call(console, "POST", PREFIX, {**QUESTION, "request_id": "r-1", "year_from": 2018})
    assert made.status == 202
    run = made.body["run"]
    assert run["status"] == "queued" and run["options"]["year_from"] == 2018 and run["review"]["on"]
    again = call(console, "POST", PREFIX, {**QUESTION, "request_id": "r-1"})
    assert (again.status, again.body["run"]["run_id"]) == (200, run["run_id"])
    assert [r["run_id"] for r in call(console, "GET", PREFIX).body["runs"]] == [run["run_id"]]
    one = f"{PREFIX}/{run['run_id']}"
    assert call(console, "GET", one).body["run"]["question"] == QUESTION["question"]
    assert call(console, "GET", f"{one}/plan").status == 404
    assert call(console, "GET", f"{one}/citations").body == {"citations": [], "fetched_at": None}
    assert call(console, "GET", f"{one}/field").body == {"papers": [], "citations": [], "fetched_at": None}
    assert call(console, "GET", f"{PREFIX}/run-nope/field").body == {"error": "No such question run"}
    early = call(console, "POST", f"{one}/pause", {})
    assert (early.status, early.body) == (409, {"error": "Only a running question run can be paused"})
    assert call(console, "POST", f"{one}/cancel", {"force": True}).status == 400
    cancelled = call(console, "POST", f"{one}/cancel", {})
    assert (cancelled.status, cancelled.body["run"]["status"]) == (200, "cancelled")
    page = call(console, "GET", f"{one}/events").body
    assert [e["type"] for e in page["events"]] == ["state", "state", "done"] and page["last_seq"] == 3
    assert call(console, "GET", f"{one}/events?after=2").body["events"][0]["seq"] == 3
    for bad, status in [({"question": ""}, 400), ([1], 400), ({**QUESTION, "lang": "de"}, 400)]:
        assert call(console, "POST", PREFIX, bad).status == status
    assert call(console, "GET", f"{PREFIX}/run-nope").body == {"error": "No such question run"}
    assert call(console, "PUT", PREFIX, {}).status == 405
    assert call(console, "GET", f"{one}/elsewhere").status == 404
    listed = call(console, "GET", PREFIX).body
    # The OpenAlex API (no essaies set up) has no arXiv index to offer.
    assert (listed["engines"], listed["default_engine"]) == (["openalex"], "openalex")
    assert call(console, "POST", PREFIX, {**QUESTION, "engine": "plain"}).status == 400


def test_a_finished_run_s_plan_papers_sets_overrides_and_export(data: Path) -> None:
    console = make_console(data)
    run_id = finished_run(console, data)
    one = f"{PREFIX}/{run_id}"
    run = call(console, "GET", one).body["run"]
    assert run["status"] == "done" and run["counts"]["candidates"] > 400
    assert call(console, "GET", f"{one}/plan").body["plan"]["version"] == 1
    assert call(console, "GET", f"{one}/plan?version=1").body["plan"]["schema"] == "question_plan@1"
    assert call(console, "GET", f"{one}/plan?version=x").status == 400
    assert [v["version"] for v in call(console, "GET", f"{one}/plans").body["versions"]] == [1]
    papers = call(console, "GET", f"{one}/papers").body["papers"]
    assert len(papers) == run["counts"]["candidates"]
    cited = call(console, "GET", f"{one}/citations").body
    assert cited["citations"] == [] and cited["fetched_at"]  # the recorded essaies knows no citations
    # Home's star field: the same papers without their text, and the same edges.
    field = call(console, "GET", f"{one}/field").body
    assert [r["key"] for r in field["papers"]] == [p["paper"]["key"] for p in papers]
    assert [r["decision"] for r in field["papers"]] == [p["decision"] for p in papers]
    assert field["citations"] == cited["citations"] and "abstract" not in json.dumps(field)
    plain = Endpoint(console)._handle(Request("GET", f"{one}/field", "", [("host", "127.0.0.1:8080")], "127.0.0.1"))
    packed = Endpoint(console)._handle(Request(
        "GET", f"{one}/field", "", [("host", "127.0.0.1:8080"), ("accept-encoding", "gzip, br")], "127.0.0.1"
    ))
    assert plain.get("Content-Encoding") is None and packed.get("Content-Encoding") == "gzip"
    assert packed.get("Vary") == "Accept-Encoding" and gzip.decompress(packed.body) == plain.body
    assert len(packed.body) < len(plain.body) / 3
    # Negotiated per RFC 9110: q=0 refuses gzip, `*` accepts it.
    for header, encoded in [("gzip;q=0, br", False), ("*", True), ("br, *;q=0", False), ("GZIP;q=0.5", True),
                            ("identity", False), ("gzip;q=x", False)]:
        got = Endpoint(console)._handle(Request(
            "GET", f"{one}/field", "", [("host", "127.0.0.1:8080"), ("accept-encoding", header)], "127.0.0.1"
        ))
        assert (got.get("Content-Encoding") == "gzip") is encoded, header
    gz = [("host", "127.0.0.1:8080"), ("accept-encoding", "gzip")]
    # The list too once it is 1 KB; the CSV export and smaller answers are sent as they are.
    for n in range(3):
        assert call(console, "POST", PREFIX, {**QUESTION, "question": f"{QUESTION['question']} {n}"}).status == 202
    listed = Endpoint(console)._handle(Request("GET", PREFIX, "", gz, "127.0.0.1"))
    assert listed.get("Content-Encoding") == "gzip" and json.loads(gzip.decompress(listed.body))["runs"]
    csv_reply = Endpoint(console)._handle(Request("GET", f"{one}/export.csv", "", gz, "127.0.0.1"))
    assert csv_reply.get("Content-Encoding") is None and len(csv_reply.body) > 1024
    small = Endpoint(console)._handle(Request("GET", f"{one}/plans", "", gz, "127.0.0.1"))
    assert len(small.body) < 1024 and small.get("Content-Encoding") is None
    key = next(p for p in papers if p["decision"] == "include")["paper"]["key"]
    made = call(console, "POST", f"{one}/overrides", {"key": key, "decision": "exclude", "note": "off topic"})
    assert made.status == 201 and made.body["override"]["actor"] == "local"
    assert call(console, "POST", f"{one}/overrides", {"key": key, "decision": "maybe"}).status == 400
    assert call(console, "POST", f"{one}/overrides", {"key": "doi:10.1/none", "decision": "exclude"}).status == 404
    after = {p["paper"]["key"]: p for p in call(console, "GET", f"{one}/papers").body["papers"]}
    assert after[key]["decision"] == "exclude" and after[key]["decided_by"] == "override"
    oid = made.body["override"]["override_id"]
    reverted = call(console, "POST", f"{one}/overrides/{oid}/revoke", {})
    assert reverted.status == 200 and reverted.body["override"]["revokes"] == oid
    assert len(call(console, "GET", f"{one}/overrides").body["overrides"]) == 2
    sets = call(console, "GET", f"{one}/sets").body["sets"]
    assert {"s1.candidates", "s1.screened"} <= {s["set_id"] for s in sets}
    assert call(console, "GET", f"{one}/sets/s1.candidates").body["schema"] == "paper_set@1"
    assert call(console, "GET", f"{one}/sets/nope").status == 404
    export = call(console, "GET", f"{one}/export.csv")
    assert export.status == 200 and export.header("Content-Type").startswith("text/csv")
    assert export.text.lstrip("﻿").startswith("key,title,year")
    built = call(console, "GET", f"{one}/graph")
    # Built at the end of the run; the override above was reverted, so it is current.
    assert built.status == 200 and built.body["graph"]["schema"] == "question_graph@1"
    assert built.body["stale"] is False and errors("question_graph", built.body["graph"]) == []
    call(console, "POST", f"{one}/overrides", {"key": key, "decision": "exclude"})
    assert call(console, "GET", f"{one}/graph").body["stale"] is True
    queued = call(console, "POST", PREFIX, qf_question()).body["run"]["run_id"]
    assert call(console, "GET", f"{PREFIX}/{queued}/graph").status == 404
    rerun = call(console, "POST", f"{one}/rerun", {"changes": {"rationale": "again"}})
    assert rerun.status == 202 and rerun.body["run"]["options"]["rerun_of"] == run_id


def test_graph_forms_downloads_and_rebuild(data: Path) -> None:
    """#57: the compact graph the page reads, the downloads, and a rebuild after an override."""
    import xml.etree.ElementTree as ET

    from isbe.question import graph_io

    console = make_console(data)
    run_id = finished_run(console, data)
    one = f"{PREFIX}/{run_id}"
    full = call(console, "GET", f"{one}/graph").body
    compact = call(console, "GET", f"{one}/graph?form=compact").body
    assert compact["stale"] is False and "edges" not in compact["graph"]
    # The same graph: edges spelled out again equal the full form's; the paper
    # records only lose their abstracts.
    back = graph_io.expand(compact["graph"])
    assert back["edges"] == full["graph"]["edges"]
    for a, b in zip(back["nodes"], full["graph"]["nodes"], strict=True):
        if "paper" in b:
            assert a["paper"] == {k: v for k, v in b["paper"].items() if k != "abstract"}
        else:
            assert a == b
    assert len(json.dumps(compact)) < len(json.dumps(full)) / 2
    # Home's galaxy map reads only what it draws (#115).
    mapped = call(console, "GET", f"{one}/graph?form=map").body
    assert mapped["stale"] is False and mapped["graph"] == graph_io.map_form(full["graph"])
    assert len(json.dumps(mapped)) < len(json.dumps(compact)) / 2
    assert call(console, "GET", f"{one}/graph?form=tiny").status == 400

    for route, kind in [("graph.json", "application/json"), ("graph.graphml", "application/graphml+xml"),
                        ("export.json", "application/json"), ("export.csv", "text/csv")]:
        got = call(console, "GET", f"{one}/{route}")
        assert got.status == 200 and got.header("Content-Type").startswith(kind), route
        assert got.header("Content-Disposition").startswith("attachment; filename=\"run-"), route
        # Downloads are never gzipped, even when the browser accepts it.
        gz = [("host", "127.0.0.1:8080"), ("accept-encoding", "gzip")]
        assert Endpoint(console)._handle(Request("GET", f"{one}/{route}", "", gz, "127.0.0.1")).get(
            "Content-Encoding") is None, route
    assert call(console, "GET", f"{one}/graph.json").body == full["graph"]
    tree = ET.fromstring(call(console, "GET", f"{one}/graph.graphml").raw)
    ns = {"g": graph_io.NS}
    assert len(tree.findall("g:graph/g:node", ns)) == len(full["graph"]["nodes"])
    assert len(tree.findall("g:graph/g:edge", ns)) == len(full["graph"]["edges"])
    papers = call(console, "GET", f"{one}/export.json").body
    assert papers["run_id"] == run_id and papers["papers"] == call(console, "GET", f"{one}/papers").body["papers"]

    # An override makes the graph stale; a rebuild makes it current again.
    key = next(n["id"] for n in full["graph"]["nodes"] if n["kind"] == "paper")
    call(console, "POST", f"{one}/overrides", {"key": key, "decision": "exclude"})
    assert call(console, "GET", f"{one}/graph").body["stale"] is True
    assert call(console, "POST", f"{one}/graph", {"force": True}).status == 400
    rebuilt = call(console, "POST", f"{one}/graph", {})
    assert rebuilt.status == 200 and rebuilt.body["cached"] is False
    after = call(console, "GET", f"{one}/graph").body
    assert after["stale"] is False and key not in {n["id"] for n in after["graph"]["nodes"]}
    assert call(console, "POST", f"{one}/graph", {}).body["cached"] is True
    # A run still going has its graph built when it ends.
    queued = call(console, "POST", PREFIX, qf_question()).body["run"]["run_id"]
    assert call(console, "POST", f"{PREFIX}/{queued}/graph", {}).status == 409
    # Ended before its graph stage: nothing to rebuild (the CLI builds the first one).
    assert call(console, "POST", f"{PREFIX}/{queued}/cancel", {}).status == 200
    assert call(console, "POST", f"{PREFIX}/{queued}/graph", {}).status == 404
    for route in ("graph.json", "graph.graphml"):
        assert call(console, "GET", f"{PREFIX}/{queued}/{route}").status == 404


def test_the_plan_review_through_the_api(data: Path) -> None:
    console = make_console(data)
    made = call(console, "POST", PREFIX, qf_question())
    one = f"{PREFIX}/{made.body['run']['run_id']}"
    qf.drain(data, qf.services(countdown=60))
    review = call(console, "GET", one).body["run"]["review"]
    assert review["active"] and review["ends_at"]
    held = call(console, "POST", f"{one}/hold", {}).body["run"]["review"]
    assert held == {"on": True, "active": True, "ends_at": None}
    edited = call(console, "POST", f"{one}/plan", {"changes": {"rationale": "by hand"}})
    assert edited.status == 200 and edited.body["plan"]["version"] == 2
    assert not edited.body["run"]["review"]["active"]
    assert call(console, "POST", f"{one}/plan", {"changes": {"rationale": "late"}}).status == 409
    assert call(console, "POST", f"{one}/start", {}).status == 200  # already starting
    qf.drain(data, qf.services())
    assert call(console, "POST", f"{one}/start", {}).status == 409


def test_runs_stay_in_their_workspace(data: Path) -> None:
    add_user(data, "admin", admin=True)
    hank_user = add_user(data, "hank")
    add_user(data, "ivy")
    console = make_console(data, shared=True)
    hank, ivy, admin = login(console, "hank"), login(console, "ivy"), login(console, "admin")
    made = call(console, "POST", PREFIX, QUESTION, cookie=hank)
    assert made.status == 202
    one = f"{PREFIX}/{made.body['run']['run_id']}"
    assert call(console, "GET", one, cookie=hank).status == 200
    assert call(console, "GET", one, cookie=ivy).status == 404
    assert call(console, "POST", f"{one}/cancel", {}, cookie=ivy).status == 404
    assert call(console, "GET", PREFIX, cookie=ivy).body["runs"] == []
    assert call(console, "GET", f"{one}?owner={hank_user.id}", cookie=admin).status == 200
    assert call(console, "POST", f"{one}/cancel?owner={hank_user.id}", {}, cookie=admin).status == 403
    assert call(console, "GET", one, cookie=None).status == 401
    no_csrf = call(console, "POST", PREFIX, QUESTION, cookie=hank, token="wrong")
    assert no_csrf.status == 403
    workspace = paths.workspace(data, hank_user.id)
    with closing(db.connect(workspace / "isbe.db")) as conn:
        assert runs.get(conn, made.body["run"]["run_id"])["status"] == "queued"


def stream_reply(console, path: str, last: str | None = None):
    headers = [("host", "127.0.0.1:8080"), ("accept", "text/event-stream")]
    if last is not None:
        headers.append(("last-event-id", last))
    return Endpoint(console)._handle(Request("GET", path, "", headers, "127.0.0.1"))


def parse(raw: bytes) -> list[tuple[int, str, dict]]:
    out = []
    for block in raw.decode().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(":"))
        if "id" in fields:
            out.append((int(fields["id"]), fields["event"], json.loads(fields["data"])))
    return out


def test_the_event_stream_sends_each_event_once_and_resumes_after_the_last_id(data: Path) -> None:
    console = make_console(data)
    run_id = call(console, "POST", PREFIX, QUESTION).body["run"]["run_id"]
    with closing(db.connect(data / "isbe.db")) as conn:
        runs.emit(conn, run_id, [("stage", {"stage": "plan", "state": "begin"})])
    reply = stream_reply(console, f"{PREFIX}/{run_id}/events")
    assert reply.status == 200 and reply.get("Content-Type").startswith("text/event-stream")
    assert reply.body == b"retry: 3000\n\n" and reply.stream is not None
    first = parse(reply.stream.poll())
    assert [(seq, kind) for seq, kind, _ in first] == [(1, "state"), (2, "stage")]
    assert first[1][2]["schema"] == "question_event@1"
    assert reply.stream.poll() == b""
    with closing(db.connect(data / "isbe.db")) as conn:
        runs.emit(conn, run_id, [("stage", {"stage": "plan", "state": "end"})])
    assert [seq for seq, _, _ in parse(reply.stream.poll())] == [3]
    resumed = stream_reply(console, f"{PREFIX}/{run_id}/events", last="2")
    assert [seq for seq, _, _ in parse(resumed.stream.poll())] == [3]
    assert stream_reply(console, f"{PREFIX}/{run_id}/events", last="x").status == 400


def test_the_event_stream_over_a_real_connection(live) -> None:
    server = live()
    console = server.console
    run_id = call(console, "POST", PREFIX, QUESTION, host=server.host).body["run"]["run_id"]
    with closing(server.connect()) as sock:
        sock.sendall(
            f"GET {PREFIX}/{run_id}/events HTTP/1.1\r\nHost: {server.host}\r\n"
            f"Accept: text/event-stream\r\nX-ISBE-CSRF: {TOKEN}\r\n\r\n".encode()
        )
        received = read_until(sock, b"id: 1\n")
        assert b"200 OK" in received.split(b"\r\n", 1)[0] and b"text/event-stream" in received
        with closing(db.connect(console.root / "isbe.db")) as conn:
            runs.emit(conn, run_id, [("stage", {"stage": "plan", "state": "begin"})])
        received += read_until(sock, b"id: 2\n")
    # Chunked: the events arrive whole, each once.
    assert received.count(b"id: 1\nevent: state\n") == 1 and received.count(b"id: 2\nevent: stage\n") == 1


def read_until(sock: socket.socket, marker: bytes, timeout: float = 10) -> bytes:
    got = b""
    deadline = time.monotonic() + timeout
    sock.settimeout(timeout)
    while marker not in got:
        assert time.monotonic() < deadline, got
        chunk = sock.recv(65536)
        assert chunk, got
        got += chunk
    return got
