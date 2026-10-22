"""Helpers of the generation tests: a root data directory whose model and
paper service are a local scripted server, and stages that drive the queue.

`fake_stages()` replaces collection and triage with doubles that make the same
requests and write the same rows (essaies
search → items; everything kept, or a judge call when the topic has a
retrieval block); enrichment and the digest are the real stages.
"""

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from isbe.core import db, http, paths
from isbe.core.llm import Llm, Prompt, Tier
from isbe.generation import Endpoints, Job, Manager, Stages
from isbe.topics import Topic

OWNER = "0123456789abcdef0123456789abcdef"
TOPIC = "id: {id}\nlabel: Papers\ncadence: weekly\narxiv:\n  categories: [cs.LG]\n  include_keywords: [learning]\n"
REPORT = "## TL;DR\nOne paper.\n\n## Papers\n### [2610.12345] Test learning\n- Plain: We study learning.\n"


def settings_for(base: str) -> dict[str, str]:
    return {
        "ISBE_LLM_PROVIDER": "openai",
        "ISBE_LLM_NO_AUTH": "true",
        "ISBE_LLM_BASE_URL": base,
        "ISBE_LLM_MODEL_SMART": "first-model",
        # A held request must not time out and be retried under load.
        "ISBE_LLM_TIMEOUT_S": "30",
        "ISBE_FULLTEXT_EXTRACTOR": "off",
        "ESSAIES_URL": base,
    }


def make_root(path: Path, base: str = "http://127.0.0.1:9") -> Path:
    """A data root with topic `papers` and settings pointing at `base`."""
    (path / "topics").mkdir(parents=True)
    copy_topic(path, "papers")
    (path / "settings.json").write_text(json.dumps(settings_for(base)))
    return path


def copy_topic(data: Path, id: str) -> None:
    (data / "topics" / f"{id}.yaml").write_text(TOPIC.format(id=id))


def response(text: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10},
    }


def papers() -> dict[str, Any]:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return {
        "items": [
            {
                "id": "2610.12345",
                "title": "Test learning",
                "abstract": "We study learning.",
                "update_date": today,
                "categories": "cs.LG",
            }
        ]
    }


@dataclass
class Request:
    path: str
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


type Answer = Callable[[int, Request], tuple[int, Any]]


@dataclass
class Script:
    """A local server answering request `i` with `answer(i, request)` → (status, JSON)."""

    answer: Answer
    requests: list[Request] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    base: str = ""

    def handle(self, req: BaseHTTPRequestHandler) -> None:
        length = int(req.headers.get("Content-Length") or 0)
        seen = Request(req.path, dict(req.headers.items()), req.rfile.read(length) if length else b"")
        with self.lock:
            i = len(self.requests)
            self.requests.append(seen)
        status, payload = self.answer(i, seen)
        body = payload if isinstance(payload, str) else json.dumps(payload)
        req.send_response(status)
        req.send_header("Content-Type", "application/json")
        req.send_header("Content-Length", str(len(body.encode())))
        req.end_headers()
        req.wfile.write(body.encode())

    @property
    def count(self) -> int:
        with self.lock:
            return len(self.requests)




def _window(topic: Topic) -> str:
    return topic.since(datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")


def fake_collect(conn: Any, topic: Topic, endpoints: Endpoints, stats: dict[str, Any]) -> None:
    """The essaies (or arXiv API) collector's requests and rows, for the mock server."""
    assert topic.arxiv is not None
    with http.Http(30) as client:
        if endpoints.essaies is None:
            text = client.get(endpoints.arxiv_api, params={"search_query": "all"}).text
            if "arxiv.org/api/errors" in text:
                raise RuntimeError("arxiv: arXiv API error: invalid search query")
            rows: Any = []
        else:
            reply = client.get(
                endpoints.essaies.rstrip("/") + "/api/search",
                params={"q": "learning", "since": _window(topic)[:10], "sort": "date", "limit": "2000"},
            ).json()
            rows = reply.get("items") if isinstance(reply, dict) else None
            if not isinstance(rows, list):
                raise RuntimeError("essaies: essaies response has no items array")
    for row in rows:
        item = f"arxiv:{row['id']}"
        conn.execute(
            "INSERT OR REPLACE INTO items (id, kind, source, title, summary, url, authors, lang, published_at, extra) "
            "VALUES (?, 'paper', 'essaies', ?, ?, ?, '[]', 'en', ?, ?)",
            (
                item, row["title"], row["abstract"], f"https://arxiv.org/abs/{row['id']}",
                f"{row['update_date']}T00:00:00Z", json.dumps({"categories": row["categories"].split()}),
            ),
        )
        conn.execute(
            "INSERT OR IGNORE INTO topic_items (topic_id, item_id, first_seen) VALUES (?, ?, ?)",
            (topic.id, item, db.now()),
        )
    stats["found"] = {"essaies" if endpoints.essaies else "arxiv": len(rows)}
    stats["new"] = len(rows)


def fake_triage(conn: Any, llm: Llm, run_id: str, topic: Topic, stats: dict[str, Any]) -> None:
    """Triage rows: everything kept without a retrieval block; with one,
    kept as unjudged and then judged (a failed judge fails the run)."""
    ids = [
        r[0]
        for r in conn.execute(
            "SELECT i.id FROM topic_items ti JOIN items i ON i.id = ti.item_id "
            "WHERE ti.topic_id = ? AND i.kind != 'repo' AND i.published_at >= ?",
            (topic.id, _window(topic)),
        )
    ]
    reason = "no retrieval block: everything is kept" if topic.retrieval is None else "not judged yet"
    for id in ids:
        conn.execute(
            "INSERT OR REPLACE INTO triage (topic_id, item_id, contract_sha, verdict, tier, reason, judged_at) "
            "VALUES (?, ?, '', 'kept', 'core', ?, ?)",
            (topic.id, id, reason, db.now()),
        )
    stats["kept"] = len(ids)
    if topic.retrieval is not None and ids:
        llm.complete(conn, run_id, Prompt("judge", Tier.FAST, "judge", json.dumps(ids), 8000))


def fake_stages(**replace: Any) -> Stages:
    return Stages(**{"collect": fake_collect, "triage": fake_triage, **replace})


def finished(manager: Manager, id: str, timeout: float = 15) -> Job:
    deadline = time.monotonic() + timeout
    while True:
        job = manager.get(id)
        assert job is not None
        if job.state not in ("queued", "running"):
            return job
        assert time.monotonic() < deadline, f"worker did not finish: {job}"
        time.sleep(0.01)


def workspace(root: Path, owner: str = OWNER) -> Path:
    return paths.workspace(root, owner)


def count(path: Path, sql: str, *args: Any) -> int:
    conn = db.connect(path)
    try:
        return int(conn.execute(sql, args).fetchone()[0])
    finally:
        conn.close()
