"""The planning prompt against a real model over the sample questions: Strata
or any OpenAI-compatible service, or the Claude API. Skipped unless a model is
given:

    ISBE_LIVE_LLM_URL=http://<host>/v1 ISBE_LIVE_LLM_KEY=... [ISBE_LIVE_LLM_MODEL=...] \
        uv run pytest -m live tests/py/question/test_plan_live.py -s
    ISBE_LIVE_LLM_PROVIDER=anthropic ISBE_LIVE_LLM_KEY=... [ISBE_LIVE_LLM_MODEL=...] \
        uv run pytest -m live tests/py/question/test_plan_live.py -s

Each reply is kept in tests/fixtures/question/<sample>/model.json (or
ISBE_LIVE_RECORD_FILE)
(`model_replies@1`, keyed by the hash of the system and user text) so the
offline tests can replay it, and the tokens and time are printed."""

import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from isbe.core import db, http, jsonfmt
from isbe.core.llm import Llm
from isbe.question import plan

ROOT = Path(__file__).resolve().parents[3]
SAMPLES = json.loads((ROOT / "docs" / "contracts" / "question" / "samples.json").read_text())["questions"]
# Where replies are kept; ISBE_LIVE_RECORD_DIR writes them elsewhere for a look first,
# and ISBE_LIVE_RECORD_FILE names the file in each sample directory.
FIXTURES = Path(os.environ.get("ISBE_LIVE_RECORD_DIR") or ROOT / "tests" / "fixtures" / "question")
RECORD_FILE = os.environ.get("ISBE_LIVE_RECORD_FILE") or "model.json"
PROVIDER = os.environ.get("ISBE_LIVE_LLM_PROVIDER") or "openai"
URL = os.environ.get("ISBE_LIVE_LLM_URL")
KEY = os.environ.get("ISBE_LIVE_LLM_KEY")
# What must never reach a recording that may be published.
_LEAK = re.compile(r"(?:/home/|/mnt/|localhost|\b(?:10|127)\.\d+\.\d+\.\d+|\b192\.168\.\d+\.\d+|\bsk-[A-Za-z0-9_-]{16,})")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (URL if PROVIDER == "openai" else KEY),
        reason="set ISBE_LIVE_LLM_URL (OpenAI-compatible) or ISBE_LIVE_LLM_KEY (anthropic) to plan with a real model",
    ),
]


def live_model() -> Llm:
    """Built from the ISBE_LIVE_LLM_* variables only, so the developer's own model
    settings (which the test suite hides) never decide what is recorded."""
    model = os.environ.get("ISBE_LIVE_LLM_MODEL")
    env = {
        "ISBE_LLM_PROVIDER": PROVIDER,
        "ISBE_LLM_BASE_URL": URL,
        "ANTHROPIC_API_KEY" if PROVIDER == "anthropic" else "OPENAI_API_KEY": KEY,
        "ISBE_LLM_NO_AUTH": "true" if PROVIDER == "openai" and not KEY else None,
        "ISBE_LLM_MODEL_SMART": model or (None if PROVIDER == "anthropic" else "default"),
        "ISBE_LLM_EXTRA_BODY": os.environ.get("ISBE_LIVE_LLM_EXTRA_BODY"),
    }
    return Llm.from_vars(env.get, client=http.Http(900))


def record(sample_id: str, conn) -> None:
    path = FIXTURES / sample_id / RECORD_FILE
    doc = json.loads(path.read_text()) if path.exists() else {"schema": "model_replies@1", "replies": []}
    for prompt, response in conn.execute("SELECT prompt, response FROM llm_calls ORDER BY rowid"):
        sent = jsonfmt.loads(prompt)
        assert not _LEAK.search(response), f"{sample_id}: the reply looks like it names a host or key"
        sha = hashlib.sha256(json.dumps([sent["system"], sent["user"]], ensure_ascii=False).encode()).hexdigest()
        doc["replies"] = [r for r in doc["replies"] if r.get("prompt_sha") != sha]
        doc["replies"].append({"purpose": "plan", "prompt_sha": sha, "reply": response})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")


@pytest.mark.parametrize("sample", SAMPLES, ids=[s["id"] for s in SAMPLES])
def test_the_model_plans_each_sample_question(sample) -> None:
    conn = db.connect(":memory:")
    doc = plan.generate(
        conn, live_model(), "live-" + sample["id"], sample["question"], sample["lang"],
        year_from=sample["year_from"], year_to=sample["year_to"],
    )
    record(sample["id"], conn)
    usage = doc.get("llm", {})
    print(f"\n{sample['id']}: {doc['source']}, {len(doc['queries'])} queries, "
          f"{usage.get('input_tokens')}+{usage.get('output_tokens')} tokens, {usage.get('duration_ms')} ms")
    for q in doc["queries"]:
        print(f"  {q['priority']} {q['layer']:<15} {q['query']}")
    assert doc["source"] == "model", doc["rationale"]
    assert 1 <= len(doc["queries"]) <= plan.MAX_QUERIES
    assert doc["criteria"]["include"]
