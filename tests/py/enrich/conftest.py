"""Fixtures of the enrich tests. Helpers are fixtures because test directories
are not packages: a test module cannot import another one."""

import json
import sqlite3
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler
from typing import Any

import pytest

from isbe.core import db
from isbe.core.llm import Llm

type AddPaper = Callable[..., None]
type Chat = Callable[..., bytes]
type LocalLlm = Callable[..., Llm]
type ReadJson = Callable[[BaseHTTPRequestHandler], Any]


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def add_paper(conn: sqlite3.Connection) -> AddPaper:
    def add(id: str, *, published: str = "2024-01-01", extra: str = "{}") -> None:
        conn.execute(
            "INSERT INTO items (id, kind, source, title, url, lang, published_at, extra) "
            "VALUES (?, 'paper', 'arxiv', 'Test', 'https://arxiv.org', 'en', ?, ?)",
            (id, published, extra),
        )

    return add


@pytest.fixture
def chat() -> Chat:
    """An OpenAI-compatible chat completion answering `content` (a JSON value or text)."""

    def answer(content: object, finish: str = "stop", tokens: int = 60) -> bytes:
        text = content if isinstance(content, str) else json.dumps(content)
        return json.dumps({
            "choices": [{"message": {"content": text}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": tokens},
        }).encode()

    return answer


@pytest.fixture
def read_json() -> ReadJson:
    def read(req: BaseHTTPRequestHandler) -> Any:
        return json.loads(req.rfile.read(int(req.headers.get("Content-Length", "0"))))

    return read


@pytest.fixture
def local_llm() -> LocalLlm:
    def make(base: str, **values: str) -> Llm:
        env = {"ISBE_LLM_PROVIDER": "openai", "ISBE_LLM_BASE_URL": base,
               "ISBE_LLM_NO_AUTH": "true", "ISBE_LLM_TIMEOUT_S": "5"}
        env.update(values)
        return Llm.from_vars(env.get)

    return make
