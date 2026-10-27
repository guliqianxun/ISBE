"""The question research routes, `/api/question/*` (docs/question/api.md).

Reached from `shared` with the caller's workspace (an administrator may read
another's with `?owner=`, GET only), after the Host, CSRF and session guards.
Runs live in the workspace; the queue they wait in is the service root's, and a
run is only ever reached through the workspace that holds it. Errors are
`{"error": "..."}`. The event stream is server-sent events when the client asks
for `text/event-stream`, else a JSON page of events.
"""

from pathlib import Path
from sqlite3 import Connection
from typing import Any

from isbe import auth
from isbe.core import db, jsonfmt
from isbe.core.errors import HttpError
from isbe.question import (
    citations,
    compose,
    graph,
    graph_io,
    queue,
    runs,
    screen,
    service,
    sets,
    sources,
)
from isbe.question import plan as plans

from .console import Console
from .wire import (
    Reply,
    Request,
    Stream,
    error,
    gzipped,
    json_reply,
    not_found,
    reply,
    request_json,
)

PREFIX = "/api/question/runs"
# Events per server-sent chunk, and the most a JSON page returns.
CHUNK = 500
PAGE = 5000
_ACTIONS = ("start", "hold", "pause", "resume", "cancel")


def route(console: Console, conn: Connection, data: Path, req: Request, user: auth.User, owner: str) -> Reply:
    try:
        return _route(console, conn, data, req, user, owner)
    except HttpError as e:
        return error(e.status, e.message)


def _route(console: Console, conn: Connection, data: Path, req: Request, user: auth.User, owner: str) -> Reply:
    path, method, root = req.path, req.method, console.root
    if path == PREFIX:
        if method == "GET":
            return gzipped(req, json_reply(200, {
                "runs": service.listing(root, conn),
                "engines": [e for e in compose.QUESTION_ENGINES if e in sources.engines()],
                "default_engine": compose.DEFAULT,
            }))
        if method == "POST":
            body = _object(req)
            run, created = service.ask(root, conn, owner, body)
            _wake(console)
            return json_reply(202 if created else 200, {"run": run})
        return _not_allowed()
    if not path.startswith(PREFIX + "/"):
        return not_found()
    parts = path.removeprefix(PREFIX + "/").split("/")
    run_id, rest = parts[0], parts[1:]
    if method == "GET":
        # One snapshot: a run deleted meanwhile is a 404, never half read (its
        # rows without the paper records they refer to).
        with db.snapshot(conn):
            runs.get(conn, run_id)  # 404 unless the run is in this workspace
            task = queue.find(root, run_id)
            if task is not None and task.owner != owner:
                return not_found()
            found = _read(conn, data, req, run_id, rest, root)
        # Downloads go as they are: a browser saving one keeps the bytes it was sent.
        return found if rest and rest[-1] in DOWNLOADS else gzipped(req, found)
    runs.get(conn, run_id)  # 404 unless the run is in this workspace
    task = queue.find(root, run_id)
    if task is not None and task.owner != owner:
        return not_found()
    if method == "DELETE" and rest == []:
        service.delete(root, conn, owner, run_id)
        return reply(204, "text/plain; charset=utf-8", b"")
    if method != "POST":
        return _not_allowed()
    body = _object(req)
    match rest:
        case ["graph"]:
            # Builds the graph again after overrides or new citations made it stale.
            if body != {}:
                return error(400, "A rebuild takes no options")
            if runs.get(conn, run_id)["status"] not in runs.ENDED:
                return error(409, "The graph is built when the run ends")
            # Only a graph that exists is built again; a run that ended before its
            # graph stage gets one from `isbe question-graph`.
            if graph.get(conn, run_id) is None:
                return error(404, "The graph of this run is not built yet")
            built = graph.build(conn, run_id)
            return json_reply(200, {"graph": graph.summary(built.doc), "cached": built.cached})
        case [action] if action in _ACTIONS:
            if body != {}:
                return error(400, "This action takes no options")
            _act(root, conn, run_id, action)
            _wake(console)
            return json_reply(200, {"run": service.summary(root, conn, run_id)})
        case ["plan"]:
            plan = service.edit_plan(root, conn, run_id, body)
            _wake(console)
            return json_reply(200, {"plan": plan, "run": service.summary(root, conn, run_id)})
        case ["rerun"]:
            run = service.rerun(root, conn, owner, run_id, user.username, body)
            _wake(console)
            return json_reply(202, {"run": run})
        case ["overrides"]:
            unknown = sorted(set(body) - {"key", "decision", "note"})
            if unknown or not isinstance(body.get("key"), str) or not isinstance(body.get("note", ""), str):
                return error(400, "An override is {key, decision, note}")
            done = screen.override(conn, run_id, body["key"], body.get("decision", ""), user.username,
                                   body.get("note", ""))
            return json_reply(201, {"override": done})
        case ["overrides", override_id, "revoke"]:
            if set(body) - {"note"} or not isinstance(body.get("note", ""), str):
                return error(400, "A revert takes only a note")
            done = screen.revoke(conn, run_id, override_id, user.username, body.get("note", ""))
            return json_reply(200, {"override": done})
    return not_found()


def _act(root: Path, conn: Connection, run_id: str, action: str) -> None:
    match action:
        case "start":
            queue.start(root, run_id)
        case "hold":
            queue.hold(root, run_id)
        case "pause":
            queue.pause(root, conn, run_id)
        case "resume":
            queue.resume(root, conn, run_id)
        case "cancel":
            queue.cancel(root, conn, run_id)


def _read(conn: Connection, data: Path, req: Request, run_id: str, rest: list[str], root: Path) -> Reply:
    match rest:
        case []:
            return json_reply(200, {"run": service.summary(root, conn, run_id)})
        case ["events"]:
            return _events(conn, data, req, run_id)
        case ["plan"]:
            version = _int(req.arg("version")) if req.arg("version") else None
            return json_reply(200, {"plan": plans.get(conn, run_id, version)})
        case ["plans"]:
            return json_reply(200, {"versions": plans.versions(conn, run_id)})
        case ["papers"]:
            return json_reply(200, {"papers": service.papers(conn, run_id)})
        case ["citations"]:
            return json_reply(200, citations.edges(conn, run_id))
        case ["field"]:
            return json_reply(200, _field(conn, run_id))
        case ["overrides"]:
            return json_reply(200, {"overrides": screen.overrides(conn, run_id)})
        case ["sets"]:
            rows = conn.execute(
                "SELECT set_id, kind, made_by, size, created_at FROM paper_sets WHERE run_id = ? "
                "ORDER BY created_at, rowid",
                (run_id,),
            ).fetchall()
            keys = ("set_id", "kind", "made_by", "size", "created_at")
            return json_reply(200, {"sets": [dict(zip(keys, r, strict=True)) for r in rows]})
        case ["sets", set_id]:
            try:
                found = sets.Store(conn, run_id).get(set_id)
            except KeyError:
                return error(404, "No such paper set")
            return json_reply(200, found.doc(run_id))
        case ["export.csv"]:
            text = service.export_csv(conn, run_id)
            return _download(reply(200, "text/csv; charset=utf-8", ("﻿" + text).encode("utf-8")), f"{run_id}.csv")
        case ["export.json"]:
            papers = {"run_id": run_id, "question": runs.get(conn, run_id)["question"],
                      "papers": service.papers(conn, run_id)}
            return _download(reply(200, "application/json; charset=utf-8", jsonfmt.dumps(papers).encode("utf-8")),
                             f"{run_id}.json")
        case ["graph"] | ["graph.json"] | ["graph.graphml"]:
            built, stale = graph.read(conn, run_id)
            if built is None:
                return error(404, "The graph of this run is not built yet")
            if rest == ["graph.json"]:
                body = jsonfmt.dumps(built).encode("utf-8")
                return _download(reply(200, "application/json; charset=utf-8", body), f"{run_id}.graph.json")
            if rest == ["graph.graphml"]:
                body = graph_io.graphml(built)
                return _download(reply(200, "application/graphml+xml; charset=utf-8", body),
                                 f"{run_id}.graphml")
            form = req.arg("form") or "full"
            shapes = {"full": lambda d: d, "compact": graph_io.compact, "map": graph_io.map_form}
            if form not in shapes:
                return error(400, "form is full, compact or map")
            return json_reply(200, {"graph": shapes[form](built), "stale": stale})
    return not_found()


def _field(conn: Connection, run_id: str) -> dict[str, Any]:
    """What Home's star field needs of a run, without the papers' text: each
    candidate's key, decision, citation count and queries, and the citation edges."""
    members = service.papers(conn, run_id)
    rows = [
        {
            "key": m["paper"]["key"],
            "decision": m["decision"],
            "cited": m["paper"].get("cited_by_count") or 0,
            "queries": list(dict.fromkeys(p["query_id"] for p in m["provenance"] if p.get("query_id"))),
        }
        for m in members
    ]
    return {"papers": rows, **citations.edges(conn, run_id, members)}


def _events(conn: Connection, data: Path, req: Request, run_id: str) -> Reply:
    after = _int(req.header("last-event-id") or req.arg("after") or "0")
    if "text/event-stream" not in (req.header("accept") or ""):
        limit = min(_int(req.arg("limit") or str(PAGE)) or PAGE, PAGE)
        events = runs.events(conn, run_id, after)[:limit]
        return json_reply(200, {"events": events, "last_seq": events[-1]["seq"] if events else after})
    # The stream ends once it has sent `done` and every event after it. A client that
    # comes back with nothing newer than `done` gets 204, which tells EventSource to
    # stop reconnecting; later events (overrides) are read with a new request.
    ended = runs.ended(conn, run_id)
    if ended is not None and ended <= after and not runs.events(conn, run_id, after)[:1]:
        return Reply(204, b"", [("Cache-Control", "no-store")])
    state: dict[str, Any] = {"seq": after, "done": ended is not None and ended <= after, "conn": None}

    def poll() -> bytes | None:
        # One connection for the stream's life (B-R1); polls never overlap.
        c = state["conn"] = state["conn"] or db.connect(data / "isbe.db")
        events = runs.events(c, run_id, state["seq"])[:CHUNK]
        if not events:
            # Ended: sent `done`, or the run was deleted meanwhile.
            gone = state["done"] or c.execute("SELECT 1 FROM question_runs WHERE run_id = ?", (run_id,)).fetchone() is None
            return None if gone else b""
        state["seq"] = events[-1]["seq"]
        state["done"] = state["done"] or any(e["type"] == "done" for e in events)
        return b"".join(sse(e) for e in events)

    def close() -> None:
        if state["conn"] is not None:
            state["conn"].close()

    return Reply(
        200,
        b"retry: 3000\n\n",
        [("Content-Type", "text/event-stream; charset=utf-8"), ("Cache-Control", "no-store"),
         ("X-Accel-Buffering", "no")],
        stream=Stream(poll, close=close),
    )


def sse(event: dict[str, Any]) -> bytes:
    """One `question_event@1` as a server-sent event: its seq as the id, its type as the event name."""
    data = jsonfmt.dumps(event, sort_keys=False)
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {data}\n\n".encode()


DOWNLOADS = ("export.csv", "export.json", "graph.json", "graph.graphml")


def _download(response: Reply, name: str) -> Reply:
    return response.with_header("Content-Disposition", f'attachment; filename="{name}"')


def _object(req: Request) -> dict[str, Any]:
    body = request_json(req) if req.body not in (b"",) else {}
    if not isinstance(body, dict):
        raise HttpError(400, "Expected a JSON object")
    return body


def _int(text: str) -> int:
    if not text.isdigit() or len(text) > 12:
        raise HttpError(400, "Expected a whole number")
    return int(text)


def _wake(console: Console) -> None:
    wake = getattr(console.generation, "wake", None)
    if callable(wake):
        wake()


def _not_allowed() -> Reply:
    return error(405, "Use GET or POST for question runs.")
