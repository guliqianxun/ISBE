import json
import os
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from isbe.core import db, http
from isbe.core.llm import Llm
from isbe.pipeline.digest import DigestError, ReportTaken, run
from isbe.pipeline.digest.load import figure_panels
from isbe.pipeline.digest.run import period, tables
from isbe.topics import Cadence, Topic, parse


def test_digest_figure_panels_accept_legacy_and_groups_but_only_local_images() -> None:
    assert figure_panels({"file": "figures/paper.png", "caption": "Figure 1"}) == (["figures/paper.png"], "Figure 1")
    grouped = {
        "files": ["figures/panel-a.png", "figures/panel-b.webp"],
        "caption": "Figure 2",
        "source": "https://arxiv.org/html/2610.00001#figure-2",
    }
    assert figure_panels(grouped) == (["figures/panel-a.png", "figures/panel-b.webp"], "Figure 2")
    for file in [
        "https://example.org/image.png",
        "//example.org/image.png",
        "../figures/image.png",
        "figures/../private.png",
        "figures/a/b.png",
        "figures/.hidden.png",
        "figures/%2e%2e.png",
        "figures/image.png)![injected](https://example.org/track.png",
        "figures/image.svg",
        "figures/",
        "figures/é.png",
    ]:
        assert figure_panels({"file": file}) is None, file
        assert figure_panels({"files": [file]}) is None, file
    assert figure_panels({"files": []}) is None
    # A group, even an empty one, wins over a legacy file; no caption reads as empty.
    assert figure_panels({"files": [], "file": "figures/a.png"}) is None
    assert figure_panels({"files": "x", "file": "figures/a.png", "caption": 3}) == (["figures/a.png"], "")
    assert figure_panels([]) is None


def test_digest_tables_take_whole_lines_within_the_cap() -> None:
    text = "## Page 1\n\nprose\n\n### Table 1: R.\n| a |\n| 1 |\n\n### Table 2: S.\n| b |"
    assert tables(text, 100) == "Table 1: R.\n| a |\n| 1 |\n\nTable 2: S.\n| b |\n\n"
    assert tables(text, 30) == "Table 1: R.\n| a |\n| 1 |\n\n…\n"
    assert tables(text, 20) == "Table 1: R.\n| a |\n…\n"
    assert tables(text, 3) == ""
    assert tables("no tables", 100) == ""


def test_digest_weekly_period_names_the_week_just_covered() -> None:
    # Monday 2026-10-12 reports on W41 (Oct 5–11); Tuesday already on W42.
    assert period(Cadence.WEEKLY, date(2026, 10, 12))[0] == "2026-W41"
    assert period(Cadence.WEEKLY, date(2026, 10, 13))[0] == "2026-W42"
    assert period(Cadence.DAILY, date(2026, 10, 13)) == ("2026-10-13", "daily")
    assert period(Cadence.WEEKLY, date(2027, 1, 4)) == ("2026-W53", "weekly")


CARD = {
    "fields": {
        "method": {"value": "Latent diffusion", "quote": "We use\n  latent   diffusion."},
        "results": {"value": "CSI@SEVIR vs DGMR: 0.40→0.47", "quote": "| DiffCast | 0.47 |"},
    },
    "rejected": ["data"],
}


def reply_for(ids: list[str], *, draft: str = "topics/t.theses.md") -> str:
    blocks = "".join(f"### [{i}v1]\n- Verdict: good\n- Rating: read in full\n" for i in ids)
    return (
        f"## TL;DR\n- Diffusion leads\n## Papers\n{blocks}## Repos\n- [o/r] active\n"
        f"## Glossary\n- CSI: hit rate\n## Analysis\nSteady.\n## Memory drafts\n- DRAFT[{draft}]: New thesis\n"
        "- DRAFT[../escape.md]: refused\n"
    )


def digest(store: Any, llm: Any, topic: Topic) -> dict[str, Any]:
    return db.job(store.conn, "digest", topic.id, lambda run_id, stats: run(store.conn, llm, run_id, topic, store.data, stats))


def test_digest_run_writes_report_row_drafts_and_stats(
    store: Any, scripted: Any, make_topic: Callable[..., Topic]
) -> None:
    topic = make_topic(repos=("o/r", "never/collected"), extra="digest: {max_papers: 2}\n")
    store.paper("2610.00001", "DiffCast: diffusion nowcasting", card=CARD,
                body="## Page 1\n\nprose\n\n### Table 1: R.\n| a |", figure={"files": ["figures/a.png"], "caption": "Fig"})
    store.paper("2610.00002", "Second", tier="secondary")
    store.paper("2610.00003", "Third", tier="secondary")
    store.paper("2610.00004", "Dropped", verdict="judge_dropped")
    store.paper("2600.00009", "Too old", published="2020-01-01T00:00:00Z")
    store.repo("o/r", pushed="2099-01-01T00:00:00Z")
    (store.data / "memory" / "topics").mkdir()
    (store.data / "memory" / "topics" / "t.md").write_text("---\nname: t\ntype: topic\nrevision: 3\n---\nThesis\n")
    model = scripted(lambda purpose, user: "[]" if purpose == "reviewer" else reply_for(["2610.00001", "2610.00002"]))
    stats = digest(store, model.llm, topic)

    rel = stats["report"]
    assert rel.startswith("reports/t/") and rel.endswith(".md")
    period_name = rel.removeprefix("reports/t/").removesuffix(".md")
    assert stats == {
        "report": rel, "papers": 2, "reviewed": 2, "listed": 1, "fulltext": 1, "cards": 1, "repos": 1,
        "drafts": 1, "flagged": 0, "second": None, "rewritten": False,
    }
    md = (store.data / rel).read_text()
    assert md.startswith(f"# Nowcasting — {period_name}\n")
    assert "- 3 papers / 2 worth reading in full: DiffCast, [2610.00002] (the first 2 reviewed" in md
    assert "### [2610.00002] Second · secondary" in md and "- [2610.00003] Third — https://arxiv.org/abs/2610.00003\n" in md
    assert "- **Method**: Latent diffusion\n" in md and "- **Method**: “We use latent diffusion.”\n" in md
    assert "![Figure](../../figures/a.png)\n<sub>Fig</sub>\n" in md
    assert "| [o/r](https://github.com/o/r) | 9 | 2099-01-01 | active |" in md
    assert "- DRAFT[topics/t.theses.md]: New thesis\n" in md and "escape" not in md
    assert "- **memory snapshot**: t@rev3\n" in md
    assert "- **triage**: 4 in window → 0 gated out · 1 judged out · 3 kept\n" in md
    assert os.stat(store.data / rel).st_mode & 0o777 == 0o644 & ~_umask()

    row = store.conn.execute("SELECT * FROM reports").fetchone()
    call = store.conn.execute("SELECT id, prompt FROM llm_calls WHERE purpose = 'digest'").fetchone()
    assert (row["id"], row["topic_id"], row["period"], row["kind"], row["path"]) == (
        f"t/{period_name}/weekly", "t", period_name, "weekly", rel)
    assert json.loads(row["fingerprint"]) == {
        "llm_call": call["id"], "memory": {"t": 3}, "papers": ["2610.00001", "2610.00002"],
        "listed": ["2610.00003"], "repos": ["o/r"],
    }
    assert row["fingerprint"].startswith('{"listed":["2610.00003"],"llm_call":')
    assert f"- **llm call**: {call['id']}\n" in md and f"- **generated_at**: {row['created_at']}\n" in md

    prompt = json.loads(call["prompt"])
    assert prompt["max_tokens"] == 24000 and prompt["system"].startswith("You are ISBE's digest writer.")
    user = prompt["user"]
    assert user.startswith(f"Topic: Nowcasting\nPeriod: {period_name}\n\n=== Facts (this period) ===\nWindow: ")
    assert "Papers (2):\n- [2610.00001] DiffCast: diffusion nowcasting (cs.CV) — https://arxiv.org/abs/2610.00001\n" in user
    assert "  Full-text facts (verified): Method=Latent diffusion | Results=CSI@SEVIR vs DGMR: 0.40→0.47\n" in user
    assert "  Tables from the full text (as parsed from the PDF):\nTable 1: R.\n| a |\n\n" in user
    assert "\nTracked repos (1):\n- [o/r] stars=9 last_push=2099-01-01 pushed_this_period=yes — https://github.com/o/r\n" in user
    assert "\n=== Memory (current) ===\n--- t@rev3 (type=topic) ---\nThesis\n\n\nWrite the six sections" in user

    pending = (store.data / "memory" / ".pending" / "topics" / "t.theses.md").read_text()
    assert pending.endswith("New thesis\n") and f"digest draft: t {period_name}" in pending

    # A second run replaces its own report and row.
    stats = digest(store, model.llm, topic)
    assert store.conn.execute("SELECT count(*) FROM reports").fetchone()[0] == 1


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def test_digest_run_fails_but_keeps_a_report_that_skipped_papers(
    store: Any, scripted: Any, make_topic: Callable[..., Topic]
) -> None:
    topic = make_topic(lang="zh", cadence="daily")
    store.paper("2610.00001", "One")
    store.paper("2610.00002", "Two")
    model = scripted(lambda purpose, user: "[]" if purpose == "reviewer" else reply_for(["2610.00001"]))
    with pytest.raises(DigestError, match="^the reply reviewed 1 of 2 papers; the report lists the rest unreviewed$"):
        digest(store, model.llm, topic)
    row = store.conn.execute("SELECT status, stats FROM runs").fetchone()
    assert row["status"] == "failed" and json.loads(row["stats"])["reviewed"] == 1
    (rel,) = store.conn.execute("SELECT path FROM reports").fetchone()
    assert rel == f"reports/t/{rel.split('/')[-1]}" and "（逐篇评审前" not in (store.data / rel).read_text()


def test_digest_rewrite_beyond_the_model_context_is_fitted_not_failed(
    store: Any, make_topic: Callable[..., Topic]
) -> None:
    # The 2026-10-21 nowcasting failure: the first draft just fit the server's
    # context, and the rewrite (the same input plus the reviewer's notes) did not.
    # The server is Strata with `fit_max_tokens` off: prompt + max_tokens + 8 must fit.
    topic = make_topic()
    store.paper("2610.00001", "One")
    window: list[int] = []
    asked: list[tuple[str, int]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system, user = (m["content"] for m in body["messages"])
        prompt, limit = len(system) + len(user), body["max_completion_tokens"]
        reviewer = "=== Report ===" in user
        if not window:
            window.append(prompt + limit + 8)
        asked.append(("reviewer" if reviewer else "digest", limit))
        if prompt + limit + 8 > window[0]:
            message = (f"request exceeds the context window: prompt ({prompt} tokens) + "
                       f"max tokens ({limit}) exceeds the context ({window[0]})")
            return httpx.Response(400, json={"error": {"type": "invalid_request_error", "message": message}})
        if reviewer:
            text = "[]" if "(REWRITTEN)" in user else '[{"claim": "Diffusion leads", "verdict": "drift"}]'
        else:
            text = reply_for(["2610.00001"])
            if "=== Reviewer notes ===" in user:
                text = text.replace("- Diffusion leads", "- Diffusion leads (REWRITTEN)")
        time.sleep(0.002)  # call ids are `<ms>/<purpose>/<prompt hash>`
        return httpx.Response(200, json={"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})

    env = {"ISBE_LLM_PROVIDER": "openai", "ISBE_LLM_BASE_URL": "http://127.0.0.1:9/v1", "ISBE_LLM_NO_AUTH": "true"}
    model = Llm.from_vars(env.get, client=http.Http(5, transport=httpx.MockTransport(handle)))
    stats = digest(store, model, topic)

    assert [purpose for purpose, _ in asked] == ["digest", "reviewer", "digest", "digest", "reviewer"]
    assert asked[2][1] == 24_000 and 0 < asked[3][1] < 24_000
    assert (stats["flagged"], stats["rewritten"], stats["second"]) == (1, True, 0)
    assert "- Diffusion leads (REWRITTEN)" in (store.data / stats["report"]).read_text()
    assert store.conn.execute("SELECT status FROM runs").fetchone()["status"] == "ok"


def test_digest_run_refuses_articles_topics(store: Any, scripted: Any) -> None:
    topic = parse("id: t\nlabel: L\ncadence: weekly\nrss: {feeds: [{name: a, url: 'https://e.org/f'}]}\n", "t")
    with pytest.raises(DigestError, match="articles digest is not built"):
        digest(store, scripted(lambda p, u: "").llm, topic)


def owned_by_research(store: Any, topic: Topic) -> tuple[Path, str]:
    name, _ = period(topic.cadence, datetime.now(UTC).astimezone(ZoneInfo(topic.schedule_timezone)).date())
    path = store.data / "reports" / "t" / f"{name}.md"
    path.parent.mkdir(parents=True)
    path.write_text("# research, write-once\n")
    return path, name


def test_digest_refuses_a_period_another_report_owns(
    store: Any, scripted: Any, make_topic: Callable[..., Topic]
) -> None:
    topic = make_topic(cadence="daily")
    store.paper("2610.00001", "One")
    path, name = owned_by_research(store, topic)
    store.conn.execute(
        "INSERT INTO reports VALUES (?, 't', ?, 'research', ?, '{}', 'x')", (f"t/{name}/research", name, f"reports/t/{name}.md")
    )
    model = scripted(lambda purpose, user: reply_for(["2610.00001"]))
    with pytest.raises(ReportTaken, match=f"^reports/t/{name}.md belongs to the research report t/{name}/research; refusing to overwrite it$"):
        digest(store, model.llm, topic)
    assert path.read_text() == "# research, write-once\n" and model.calls == []
    assert [tuple(r) for r in store.conn.execute("SELECT kind FROM reports")] == [("research",)]


def test_digest_refuses_a_file_no_report_owns(
    store: Any, scripted: Any, make_topic: Callable[..., Topic]
) -> None:
    topic = make_topic(cadence="daily")
    store.paper("2610.00001", "One")
    path, _ = owned_by_research(store, topic)
    model = scripted(lambda purpose, user: reply_for(["2610.00001"]))
    with pytest.raises(ReportTaken, match="exists but no report owns it"):
        digest(store, model.llm, topic)
    assert path.read_text() == "# research, write-once\n" and model.calls == []
