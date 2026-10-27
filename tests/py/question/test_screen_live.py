"""The screening stage against a real model over the sample questions' recorded
candidates (`replay.screening_run`, ~1,000 papers each). Skipped unless a model
is given:

    ISBE_LIVE_LLM_URL=http://<host>/v1 ISBE_LIVE_LLM_KEY=... \\
        ISBE_LIVE_LLM_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}}' \\
        uv run pytest -m live tests/py/question/test_screen_live.py -s

Each reply is kept in tests/fixtures/question/<sample>/model.screen.json
(`model_replies@1`, keyed by the hash of the system and user text; ISBE_LIVE_RECORD_DIR
writes them elsewhere) so `test_screen` replays the run offline. Printed per
sample: candidates, included, excluded, unscreened, must-find papers kept, calls,
tokens and time. `ISBE_LIVE_SAMPLES` picks samples (comma-separated); by
default the green-space sample is left out while its searches are re-recorded."""

import os
import time
from pathlib import Path

import httpx
import pytest
import replay

from isbe.core import http
from isbe.core.llm import Llm
from isbe.question import screen

PROVIDER = os.environ.get("ISBE_LIVE_LLM_PROVIDER") or "openai"
URL = os.environ.get("ISBE_LIVE_LLM_URL")
KEY = os.environ.get("ISBE_LIVE_LLM_KEY")
FIXTURES = Path(os.environ.get("ISBE_LIVE_RECORD_DIR") or replay.FIXTURES)
SAMPLES = [s for s in (os.environ.get("ISBE_LIVE_SAMPLES") or "").split(",") if s] or [
    s for s in replay.sample_ids() if s != "green-space-mental-health"
]

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (URL if PROVIDER == "openai" else KEY),
        reason="set ISBE_LIVE_LLM_URL (OpenAI-compatible) or ISBE_LIVE_LLM_KEY (anthropic) to screen with a real model",
    ),
]


def live_model(transport: httpx.BaseTransport) -> Llm:
    env = {
        "ISBE_LLM_PROVIDER": PROVIDER,
        "ISBE_LLM_BASE_URL": URL,
        "ANTHROPIC_API_KEY" if PROVIDER == "anthropic" else "OPENAI_API_KEY": KEY,
        "ISBE_LLM_NO_AUTH": "true" if PROVIDER == "openai" and not KEY else None,
        "ISBE_LLM_MODEL_FAST": os.environ.get("ISBE_LIVE_LLM_MODEL") or (None if PROVIDER == "anthropic" else "default"),
        "ISBE_LLM_EXTRA_BODY": os.environ.get("ISBE_LIVE_LLM_EXTRA_BODY"),
    }
    return Llm.from_vars(env.get, client=http.Http(900, transport=transport))


def kept_must_find(s: replay.Sample, conn, run_id: str) -> tuple[int, int, list[str]]:
    """Must-find papers among the candidates, those kept (include or unscreened), and
    the titles of those excluded."""
    found = screen.decisions(conn, run_id)
    papers = {k: m["paper"] for m in conn_candidates(conn, run_id) for k in m["paper"]["keys"]}
    there = kept = 0
    lost = []
    for must in s.question["must_find"]:
        paper = next((papers[k] for k in must["keys"] if k in papers), None)
        if paper is None:
            continue
        there += 1
        if found[paper["key"]]["decision"] != "exclude":
            kept += 1
        else:
            lost.append(paper["title"])
    return there, kept, lost


def conn_candidates(conn, run_id: str) -> list[dict]:
    from isbe.question import sets

    return sets.Store(conn, run_id).get("s1.candidates").members


@pytest.mark.parametrize("sample_id", SAMPLES)
def test_the_model_screens_each_sample(sample_id: str) -> None:
    s = replay.sample(sample_id)
    conn, run_id, candidates = replay.screening_run(s)
    recorder = replay.ModelRecorder(httpx.HTTPTransport(), lambda system, user: "screen")
    started = time.monotonic()
    result = screen.run(conn, live_model(recorder.transport()), run_id, candidates)
    seconds = time.monotonic() - started
    recorder.save(FIXTURES / sample_id / "model.screen.json", merge=False)
    there, kept, lost = kept_must_find(s, conn, run_id)
    print(
        f"\n{sample_id}: {len(conn_candidates(conn, run_id))} candidates, {result.included} included, "
        f"{result.excluded} excluded, {result.unscreened} unscreened; must-find kept {kept}/{there}; "
        f"{result.calls} calls ({result.batches} batches, {len(result.failed_batches)} failed), "
        f"{result.input_tokens} in / {result.output_tokens} out tokens, {seconds:.0f} s"
    )
    for title in lost:
        print(f"  lost: {title}")
    assert result.unscreened == 0
