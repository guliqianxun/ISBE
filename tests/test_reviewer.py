"""Tests for the reviewer stage (claim decomposition + one-rewrite loop)."""

import json

from isbe.topics._shared.reviewer import review_and_maybe_rewrite, review_claims

EVIDENCE = "摘要: ConvLSTM did not consistently outperform simpler alternatives."


def _reviewer(payloads: list):
    """Returns a fake reviewer fn yielding successive JSON payloads."""
    it = iter(payloads)

    def fn(system: str, user: str) -> str:
        return "```json\n" + json.dumps(next(it)) + "\n```"

    return fn


def test_review_claims_parses_and_filters():
    payload = [
        {"claim": "c1", "verdict": "supported", "note": "n"},
        {"claim": "c2", "verdict": "unsupported", "note": "n"},
        {"claim": "c3", "verdict": "bogus", "note": "n"},  # invalid verdict dropped
        "junk",
    ]
    out = review_claims(evidence=EVIDENCE, report="r", complete_fn=_reviewer([payload]))
    assert [c["verdict"] for c in out] == ["supported", "unsupported"]


def test_review_claims_fails_open_on_garbage():
    out = review_claims(evidence=EVIDENCE, report="r", complete_fn=lambda s, u: "not json")
    assert out == []


def test_no_flags_means_no_rewrite():
    writer_calls = {"n": 0}

    def writer(system, user):
        writer_calls["n"] += 1
        return "rewritten"

    text, summary = review_and_maybe_rewrite(
        system_prompt="sys",
        user_prompt="usr",
        first_text="original",
        evidence=EVIDENCE,
        writer_fn=writer,
        reviewer_fn=_reviewer([[{"claim": "c", "verdict": "supported", "note": ""}]]),
    )
    assert text == "original"
    assert summary["rewritten"] is False
    assert writer_calls["n"] == 0


def test_flagged_claim_triggers_one_rewrite_and_reaudit():
    def writer(system, user):
        assert "审核员意见" in user and "未跑赢 persistence" in user
        return "corrected report"

    first_review = [
        {"claim": "ConvLSTM 未跑赢 persistence", "verdict": "drift", "note": "证据是未能一致优于"}
    ]
    second_review = [
        {"claim": "未能一致优于简单基线", "verdict": "supported", "note": ""},
        {"claim": "残余无据断言", "verdict": "unsupported", "note": "找不到"},
    ]
    text, summary = review_and_maybe_rewrite(
        system_prompt="sys",
        user_prompt="usr",
        first_text="ConvLSTM 未跑赢 persistence",
        evidence=EVIDENCE,
        writer_fn=writer,
        reviewer_fn=_reviewer([first_review, second_review]),
    )
    assert text == "corrected report"
    assert summary["rewritten"] is True
    assert summary["flagged_first_pass"] == 1
    assert summary["flagged_residual"] == ["[unsupported] 残余无据断言"]
