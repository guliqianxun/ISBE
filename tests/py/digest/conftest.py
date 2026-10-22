import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from isbe.core import db, http
from isbe.core.llm import Llm
from isbe.topics import Topic, parse

type Answer = Callable[[str, str], str]  # (purpose, user prompt) -> reply text


@dataclass
class Scripted:
    """A real `Llm` whose provider is a mock: digest and reviewer replies come from `answer`."""

    answer: Answer
    calls: list[tuple[str, str]] = field(default_factory=list)
    llm: Llm = field(init=False)

    def __post_init__(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            purpose = "digest" if body["max_completion_tokens"] == 24_000 else "reviewer"
            user = body["messages"][1]["content"]
            self.calls.append((purpose, user))
            # Call ids are `<ms>/<purpose>/<prompt hash>`: a repeated prompt needs a new millisecond.
            time.sleep(0.002)
            reply = {"choices": [{"message": {"content": self.answer(purpose, user)}, "finish_reason": "stop"}]}
            return httpx.Response(200, json=reply)

        env = {
            "ISBE_LLM_PROVIDER": "openai",
            "ISBE_LLM_BASE_URL": "http://127.0.0.1:9/v1",
            "ISBE_LLM_NO_AUTH": "true",
        }
        self.llm = Llm.from_vars(env.get, client=http.Http(5, transport=httpx.MockTransport(handle)))

    def purposes(self) -> list[str]:
        return [purpose for purpose, _ in self.calls]


def topic_yaml(lang: str = "en", cadence: str = "weekly", repos: tuple[str, ...] = (), extra: str = "") -> Topic:
    text = f"id: t\nlabel: Nowcasting\nlang: {lang}\ncadence: {cadence}\narxiv: {{categories: [cs.CV], include_keywords: [x]}}\n"
    if repos:
        text += f"github: {{repos: [{', '.join(repos)}]}}\n"
    return parse(text + extra, "t")


def ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Store:
    conn: sqlite3.Connection
    data: Path

    def paper(
        self,
        id: str,
        title: str,
        *,
        published: str | None = None,
        summary: str | None = "We forecast rain.",
        authors: list[str] | None = None,
        tier: str = "core",
        verdict: str = "kept",
        categories: list[str] | None = None,
        body: str | None = None,
        figure: dict[str, Any] | None = None,
        card: dict[str, Any] | None = None,
        lang: str = "en",
    ) -> None:
        item = f"arxiv:{id}"
        self.conn.execute(
            "INSERT INTO items (id, kind, source, title, summary, url, authors, lang, published_at, extra) "
            "VALUES (?, 'paper', 'arxiv', ?, ?, ?, ?, 'en', ?, ?)",
            (
                item, title, summary, f"https://arxiv.org/abs/{id}", json.dumps(authors or ["A One"]),
                published or ago(1), json.dumps({"categories": categories or ["cs.CV"]}),
            ),
        )
        self.conn.execute(
            "INSERT INTO topic_items (topic_id, item_id, first_seen) VALUES ('t', ?, ?)", (item, db.now())
        )
        self.conn.execute(
            "INSERT INTO triage (topic_id, item_id, contract_sha, verdict, tier, reason, judged_at) "
            "VALUES ('t', ?, 'sha', ?, ?, 'r', ?)",
            (item, verdict, tier if verdict == "kept" else None, db.now()),
        )
        if body is not None or figure is not None:
            self.conn.execute(
                "INSERT INTO fulltext (item_id, via, body, extracted_at, figure) VALUES (?, 'arxiv-html', ?, ?, ?)",
                (item, body or "", db.now(), None if figure is None else json.dumps(figure)),
            )
        if card is not None:
            self.conn.execute(
                "INSERT INTO cards (item_id, lang, key, card, created_at) VALUES (?, ?, 'k', ?, ?)",
                (item, lang, json.dumps(card), db.now()),
            )

    def repo(self, name: str, *, stars: int | None = 9, pushed: str | None = None) -> None:
        extra: dict[str, Any] = {} if stars is None else {"stars": stars}
        if pushed is not None:
            extra["pushed_at"] = pushed
        self.conn.execute(
            "INSERT INTO items (id, kind, source, title, url, lang, published_at, extra) "
            "VALUES (?, 'repo', 'github', ?, ?, 'en', '2020-01-01T00:00:00Z', ?)",
            (f"gh:{name}", name, f"https://github.com/{name}", json.dumps(extra)),
        )


@pytest.fixture
def store(data: Path) -> Store:
    return Store(db.connect(data / "isbe.db"), data)


@pytest.fixture
def scripted() -> type[Scripted]:
    """`scripted(answer)`: a mock-provider `Llm` (`.llm`) recording `.calls`."""
    return Scripted


@pytest.fixture
def make_topic() -> Callable[..., Topic]:
    """`make_topic(lang, cadence, repos, extra)`: topic `t` with an arxiv block."""
    return topic_yaml
