"""Tests for the paper-cards extractor (regex layer + anchored LLM layer)."""

import json

import pytest

from isbe.topics._shared.paper_cards import (
    build_card,
    extract_llm_fields,
    grep_facts,
    load_or_build_card,
    verify_anchor,
)

CORPUS = (
    "# 2604.11111\n\n## [text] page 1\n\n"
    "DiffCast-XL: latent diffusion for nowcasting. "
    "Code is available at https://github.com/example/diffcast-xl. "
    "We train on SEVIR and evaluate on the KNMI dataset. "
    "All models were trained on 8×A100 GPUs for three days. "
    "Our method improves CSI from 0.41 to 0.47 over DGMR.\n"
)


def test_grep_facts_extracts_hard_facts():
    facts = grep_facts(CORPUS)
    assert facts["code_urls"] == ["https://github.com/example/diffcast-xl"]
    assert any("A100" in g for g in facts["gpu_mentions"])
    assert "SEVIR" in facts["dataset_mentions"]
    assert "KNMI" in facts["dataset_mentions"]


def test_grep_strips_trailing_punctuation():
    facts = grep_facts("see https://github.com/a/b).")
    assert facts["code_urls"] == ["https://github.com/a/b"]


def test_verify_anchor_exact_and_fuzzy():
    assert verify_anchor("improves CSI from 0.41 to 0.47 over DGMR", CORPUS)
    # whitespace differences (line wraps in corpus) must not reject
    assert verify_anchor("improves CSI from 0.41  to\n0.47 over DGMR", CORPUS)
    assert not verify_anchor("improves CSI from 0.30 to 0.99", CORPUS)
    assert not verify_anchor("short", CORPUS)  # too short to be evidence


def _fake_llm(payload: dict):
    def fn(system: str, user: str) -> str:
        return "```json\n" + json.dumps(payload) + "\n```"

    return fn


def test_extract_llm_fields_parses_fenced_json():
    payload = {
        "method": {"value": "潜空间扩散", "anchor": "latent diffusion for nowcasting"},
        "bogus_field": {"value": "x", "anchor": "y"},  # unknown → ignored
        "compute": {"value": "8×A100", "anchor": ""},  # empty anchor → dropped
    }
    fields = extract_llm_fields(CORPUS, complete_fn=_fake_llm(payload))
    assert set(fields) == {"method"}
    assert fields["method"].verified is False  # verification happens in build_card


def test_extract_llm_fields_retries_then_fails():
    calls = {"n": 0}

    def bad(system, user):
        calls["n"] += 1
        return "not json at all"

    with pytest.raises(ValueError):
        extract_llm_fields(CORPUS, complete_fn=bad)
    assert calls["n"] == 2


def test_build_card_verifies_anchors():
    payload = {
        "method": {"value": "潜空间扩散做临近预报", "anchor": "latent diffusion for nowcasting"},
        "results": {
            "value": "CSI 0.41→0.47 vs DGMR",
            "anchor": "improves CSI from 0.41 to 0.47 over DGMR",
        },
        "compute": {"value": "128×H100", "anchor": "trained on 128 H100 GPUs"},  # fabricated
    }
    card = build_card("2604.11111", CORPUS, complete_fn=_fake_llm(payload))
    assert card.fields["method"].verified
    assert card.fields["results"].verified
    assert "compute" not in card.fields
    assert card.rejected_fields == ["compute"]
    # regex layer independent of LLM behavior
    assert card.code_urls == ["https://github.com/example/diffcast-xl"]


def test_load_or_build_card_caches_by_corpus_hash(tmp_path):
    corpus_path = tmp_path / "2604.11111.metrail.md"
    corpus_path.write_text(CORPUS, encoding="utf-8")
    payload = {"method": {"value": "m", "anchor": "latent diffusion for nowcasting"}}
    calls = {"n": 0}

    def counting(system, user):
        calls["n"] += 1
        return json.dumps(payload)

    c1 = load_or_build_card("2604.11111", corpus_path, complete_fn=counting)
    c2 = load_or_build_card("2604.11111", corpus_path, complete_fn=counting)
    assert calls["n"] == 1  # second load served from cache
    assert c1.corpus_sha256 == c2.corpus_sha256
    assert (tmp_path / "2604.11111.metrail.card.json").is_file()

    corpus_path.write_text(CORPUS + "\nnew revision", encoding="utf-8")
    load_or_build_card("2604.11111", corpus_path, complete_fn=counting)
    assert calls["n"] == 2  # corpus changed → rebuilt


def test_load_or_build_card_missing_corpus_returns_none(tmp_path):
    assert load_or_build_card("x", tmp_path / "nope.metrail.md") is None
