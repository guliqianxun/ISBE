from collections.abc import Callable
from typing import Any

import pytest

from isbe.core import db
from isbe.core.llm import Reply
from isbe.pipeline.digest.review import (
    Flag,
    Review,
    checkable,
    feedback,
    parse_flags,
    run,
)
from isbe.topics import Lang

DRIFT = '[{"claim": "全面领先", "verdict": "drift", "note": "仅 CSI"}]'
TWO = '[{"claim": "a", "verdict": "drift"}, {"claim": "b", "verdict": "unsupported"}]'


def test_digest_review_leaves_glossary_and_drafts_out() -> None:
    reply = "## TL;DR\n- a\n## 名词\n- GraphCast: DeepMind 的模型\n## 分析\nb\n## 蒸馏\n- DRAFT[x.md]: c\n"
    assert checkable(reply) == "## TL;DR\n- a\n## 分析\nb\n"


def test_digest_review_reads_only_flagged_claims() -> None:
    reply = (
        '```json\n[{"claim": "全面领先", "verdict": "drift", "note": "仅 2/5 指标领先"},\n'
        '{"claim": "x", "verdict": "supported"}, {"verdict": "unsupported"}]\n```'
    )
    assert parse_flags(reply) == [Flag("drift", "全面领先", "仅 2/5 指标领先")]
    assert parse_flags("[]") == []
    assert parse_flags("no array") is None
    # a broken array is unread, not clean
    assert parse_flags('[{"claim": "x", "verdict": "drift"],]') is None
    assert parse_flags('{"a": 1} ]x[') is None and parse_flags('[1, "x", null]') == []


def draft(*ids: str, marker: str = "") -> str:
    blocks = "".join(f"### [{i}]\n- Verdict: ok{marker}\n" for i in ids)
    return f"## TL;DR\n- t{marker}\n## Papers\n{blocks}## Analysis\nx\n"


def review(
    scripted: Any,
    flags_for: Callable[[str], str],
    rewrite: str | Exception,
    papers: tuple[str, ...] = ("1", "2"),
) -> tuple[Reply, Review, Any, list[str]]:
    """Run the reviewer on `draft(*papers)`; the reviewer answers `flags_for(report)`."""
    model = scripted(lambda purpose, user: flags_for(user.split("=== Report ===\n", 1)[1]))
    conn = db.connect(":memory:")
    written: list[str] = []

    def write(user: str) -> Reply:
        written.append(user)
        if isinstance(rewrite, Exception):
            raise rewrite
        return Reply(rewrite, "c2")

    out: list[tuple[Reply, Review]] = []

    def go(run_id: str, stats: dict[str, Any]) -> None:
        out.append(run(conn, model.llm, run_id, Lang.EN, "EVIDENCE", write, Reply(draft(*papers), "c1"), list(papers)))

    db.job(conn, "digest", "t", go)
    return out[0][0], out[0][1], model, written


def test_digest_review_clean_first_draft_is_kept_without_rewrite(scripted: Any) -> None:
    reply, r, model, written = review(scripted, lambda report: "[]", "unused")
    assert reply.call_id == "c1" and written == [] and model.purposes() == ["reviewer"]
    assert r == Review()


def test_digest_review_rewrite_with_fewer_flags_is_adopted(scripted: Any) -> None:
    reply, r, _, written = review(
        scripted, lambda report: DRIFT if "REWRITE" in report else TWO, draft("1", "2", marker="REWRITE")
    )
    assert reply.call_id == "c2" and r.rewritten
    assert r.first == [Flag("drift", "a", ""), Flag("unsupported", "b", "")]
    assert r.second == [Flag("drift", "全面领先", "仅 CSI")]
    assert written == ["EVIDENCE" + feedback(Lang.EN, r.first)]
    assert written[0].endswith("- [drift] a — \n- [unsupported] b — \n")


def test_digest_review_rewrite_with_more_flags_keeps_the_first_draft(scripted: Any) -> None:
    reply, r, _, _ = review(
        scripted, lambda report: TWO if "REWRITE" in report else DRIFT, draft("1", "2", marker="REWRITE")
    )
    assert reply.call_id == "c1" and not r.rewritten and r.second is not None and len(r.second) == 2


def test_digest_review_failures_keep_the_report_and_say_so(scripted: Any) -> None:
    reply, r, _, _ = review(scripted, lambda report: DRIFT, RuntimeError("HTTP 500"))
    assert reply.call_id == "c1" and r.error == "rewrite: HTTP 500"
    # Twice unreadable is an error naming the second call; the first draft stands.
    reply, r, model, _ = review(scripted, lambda report: "no array", "unused")
    assert reply.call_id == "c1" and model.purposes() == ["reviewer", "reviewer"]
    assert r.error is not None and r.error.startswith("the reviewer's reply is not a JSON array twice; see llm_calls ")
    assert "/reviewer/" in r.error


def test_rewrite_that_drops_paper_blocks_is_not_adopted(scripted: Any) -> None:
    # The rewrite draws no flags, but only by leaving paper 2 out: the first draft stands.
    reply, r, model, _ = review(scripted, lambda report: "[]" if "REWRITE" in report else DRIFT, draft("1", marker="REWRITE"))
    assert reply.call_id == "c1" and not r.rewritten
    assert r.dropped == ["2"] and r.second is None
    # The dropped rewrite is not even reviewed.
    assert model.purposes() == ["reviewer"]
    # A rewrite that keeps every block the first draft had is still adopted, even
    # when the first draft itself missed one.
    reply, r, _, _ = review(
        scripted,
        lambda report: "[]" if "REWRITE" in report else DRIFT,
        draft("1", marker="REWRITE"),
        papers=("1",),
    )
    assert reply.call_id == "c2" and r.rewritten and r.dropped == []


def test_digest_reviewer_prompt_text(scripted: Any) -> None:
    model = scripted(lambda purpose, user: "[]")
    conn = db.connect(":memory:")

    def go(run_id: str, stats: dict[str, Any]) -> None:
        first = Reply("## 名词\n- a: b\n## 分析\nx", "c1")
        run(conn, model.llm, run_id, Lang.ZH, "证", lambda u: Reply("", ""), first, [])

    db.job(conn, "digest", "t", go)
    assert model.calls == [("reviewer", "=== 证据 ===\n证\n\n=== 报告 ===\n## 分析\nx\n")]
    row = conn.execute("SELECT purpose, prompt FROM llm_calls").fetchone()
    assert row[0] == "reviewer" and '"max_tokens":8000' in row[1]


@pytest.mark.parametrize("lang", [Lang.ZH, Lang.EN])
def test_digest_feedback_lists_flags(lang: Lang) -> None:
    text = feedback(lang, [Flag("drift", "c", "n")])
    assert text.endswith("- [drift] c — n\n") and text.startswith("\n\n=== ")
