"""Shared by the research tests: the fixture and a scripted model."""

import copy
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from isbe.core import db, http, jsonfmt
from isbe.core.llm import Llm
from isbe.reports import research

RAW = "中文前缀。公司报告2025年开展8次培训。融资成本未披露。"
TOPIC, PERIOD = "research-test", "2026-10-16-v1"


def sha(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def request(raw: str = RAW) -> dict[str, Any]:
    return {
        "topic": TOPIC,
        "title": "固定材料研究",
        "period": PERIOD,
        "scope": {
            "from": "2024-01-01", "to": "2026-10-16", "region": "上海", "languages": ["zh"],
            "questions": ["材料能支持什么结论？"],
        },
        "search_note": "仅使用指定材料，不代表完整检索。",
        "limitations": ["尚未独立语义核验"],
        "sources": [{
            "id": "S1", "title": "测试企业披露", "url": "https://example.invalid/disclosure",
            "material_type": "company_disclosure", "published_on": None, "coverage": "2025",
            "reading_depth": "excerpt", "role": "self_report", "limitations": ["企业自述"],
            "original_file": "source.txt", "sha256": sha(raw), "text_file": "source.txt",
            "excerpts": [
                {"id": "E1", "location": "第1段", "quote": "公司报告2025年开展8次培训。"},
                {"id": "E2", "location": "第2段", "quote": "融资成本未披露。"},
            ],
        }],
    }


class Fixture:
    """A data directory with an input manifest."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "input").mkdir(parents=True)
        self.input = root / "input" / "request.json"
        (root / "input" / "source.txt").write_text(RAW, encoding="utf-8")
        self.input.write_text(jsonfmt.dumps(request()), encoding="utf-8")
        self.conn = db.connect(":memory:")

    def edit(self, change: Callable[[dict[str, Any]], None]) -> None:
        value = json.loads(self.input.read_text(encoding="utf-8"))
        change(value)
        self.input.write_text(jsonfmt.dumps(value), encoding="utf-8")

    def run(self, model: Llm) -> dict[str, Any]:
        return db.job(
            self.conn,
            "research",
            TOPIC,
            lambda run_id, stats: research.run(
                self.conn, model, run_id, self.root, self.input, stats
            ),
        )

    def count(self, sql: str) -> int:
        return int(self.conn.execute(sql).fetchone()[0])


def answer() -> dict[str, Any]:
    return copy.deepcopy({"sources": [{
        "source_id": "S1",
        "subjects": [{"evidence": "E1", "text": "公司"}],
        "objects": [{"evidence": "E1", "text": "8次培训"}],
        "times": [{"evidence": "E1", "text": "2025年"}],
        "actions": [{"evidence": "E1", "text": "公司报告2025年开展8次培训。"}],
        "figures": [{"evidence": "E1", "text": "公司报告2025年开展8次培训。"}],
    }]})


def model(response: Any, *, raw: str | None = None) -> tuple[Llm, list[dict[str, Any]]]:
    """An OpenAI-compatible model answering `response`, checking what it is sent."""
    seen: list[dict[str, Any]] = []

    def handle(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/chat/completions"
        body = json.loads(req.content)
        user = json.loads(body["messages"][1]["content"])
        assert body["model"] == "research-fixture"
        assert user["sources"][0]["excerpts"][0]["id"] == "E1"
        assert "original_file" not in user["sources"][0]
        seen.append(body)
        content = raw if raw is not None else jsonfmt.dumps(response)
        reply = {
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 30},
        }
        return httpx.Response(200, content=json.dumps(reply).encode())

    env = {
        "ISBE_LLM_PROVIDER": "openai",
        "ISBE_LLM_BASE_URL": "http://127.0.0.1:9/v1",
        "ISBE_LLM_NO_AUTH": "true",
        "ISBE_LLM_MODEL_SMART": "research-fixture",
        "ISBE_LLM_TIMEOUT_S": "5",
    }
    client = http.Http(5, transport=httpx.MockTransport(handle))
    return Llm.from_vars(env.get, client=client), seen
