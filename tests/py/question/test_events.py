"""The recorded run logs the frontend replays (`<sample>/events.json`): valid
`question_event@1` events that `replay.run_events` still rebuilds the same."""

import json

import pytest
import replay
from question_schemas import errors

LOGGED = [i for i in replay.sample_ids() if (replay.FIXTURES / i / replay.EVENTS).is_file()]


@pytest.mark.parametrize("sample_id", LOGGED)
def test_recorded_run_log_is_current_and_valid(sample_id: str) -> None:
    logged = json.loads((replay.FIXTURES / sample_id / replay.EVENTS).read_text())
    assert logged == replay.run_events(replay.sample(sample_id)), "rebuild with replay.py --events"
    assert [e["seq"] for e in logged] == list(range(1, len(logged) + 1))
    assert [m for e in logged for m in errors("question_event", e)] == []
    hits = [e["data"] for e in logged if e["type"] == "hit"]
    verdicts = {e["data"]["verdict"]["key"] for e in logged if e["type"] == "verdict"}
    assert verdicts == {h["paper"]["key"] for h in hits if h["new"]} - {
        k for e in logged if e["type"] == "dedup" for k in e["data"]["merged"]
    }
    assert logged[-1]["type"] == "done"
