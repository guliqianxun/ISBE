"""Research reports: frozen sources, extraction, citations, ownership and failed calls."""

import copy
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from research_helpers import PERIOD, RAW, TOPIC, Fixture, answer, model, sha

from isbe.core import db, errors, http
from isbe.core.llm import Llm, LlmError
from isbe.reports import research
from isbe.reports import research_compat as compat
from isbe.reports.research import prepare, validate_draft
from isbe.reports.research_files import ResearchError, read
from isbe.reports.research_render import md, render
from isbe.reports.research_schema import BUNDLE, DRAFT, Draft


def draft(value: Any) -> Draft:
    """Decode `value` as a `Draft` (a parsed JSON value, not a streamed document)."""
    result: Draft = compat.decode(DRAFT, copy.deepcopy(value), streamed=False)
    return result


def test_generated_report_freezes_sources_and_uses_byte_positions(fixture: Fixture) -> None:
    response = answer()
    response["sources"][0]["objects"].append({"evidence": "E1", "text": "9次培训"})
    llm, seen = model(response)
    stats = fixture.run(llm)
    assert len(seen) == 1
    assert stats["materials"] == 1
    assert stats["rejected_spans"] == 1
    assert stats["extraction_error"] is None
    report = (fixture.root / stats["report"]).read_text(encoding="utf-8")
    assert "9次培训" not in report
    assert "> 公司报告2025年开展8次培训。" in report
    assert "/api/research/evidence?topic=research-test&period=2026-10-16-v1&id=E1" in report
    assert "尚未完成独立语义核验" not in report
    assert "发布机构未注明｜测试企业披露" in report
    e = research.evidence(fixture.conn, fixture.root, TOPIC, PERIOD, "E1")
    assert e is not None
    assert e["start"] == len("中文前缀。".encode())
    assert e["quote"] == "公司报告2025年开展8次培训。"
    assert "original_file" not in e
    found = research.original(fixture.conn, fixture.root, TOPIC, PERIOD, "S1")
    assert found is not None
    mime, raw = found
    assert mime.startswith("text/plain")
    assert sha(raw) == e["sha256"]
    assert fixture.count("SELECT count(*) FROM llm_calls WHERE purpose='research'") == 1
    with pytest.raises(ResearchError, match="already exists"):
        fixture.run(llm)
    (fixture.root / "input" / "source.txt").write_text("later changes", encoding="utf-8")
    again = research.original(fixture.conn, fixture.root, TOPIC, PERIOD, "S1")
    assert again is not None and again[1] == raw


def test_extraction_keeps_every_source_in_input_order_and_old_bundles(fixture: Fixture) -> None:
    def change(v: dict[str, Any]) -> None:
        v["sources"][0]["publisher"] = "测试企业"
        v["sources"][0]["title"] = "测试企业披露 [说明]"
        v["sources"][0]["url"] = "https://example.invalid/a(b)"
        unused = copy.deepcopy(v["sources"][0])
        unused["id"] = "S2"
        unused["title"] = "未标注材料"
        unused["excerpts"][0]["id"] = "E3"
        unused["excerpts"][1]["id"] = "E4"
        v["sources"].append(unused)

    fixture.edit(change)
    bundle, _ = prepare(fixture.input)
    value = answer()
    empty = {"source_id": "S2", "subjects": [], "objects": [], "times": [], "actions": [],
             "figures": []}
    value["sources"].insert(0, empty)
    parsed = draft(value)
    validate_draft(parsed, bundle)
    report = render(bundle, parsed)
    assert "测试企业｜测试企业披露 \\[说明\\]" in report
    for id in ["E1", "E2", "E3", "E4"]:
        assert f"&id={id})" in report
    assert report.index("测试企业披露") < report.index("未标注材料")
    assert "**主体与职责线索**：未标注" in report
    assert "> 融资成本未披露。" in report
    assert "(<https://example.invalid/a(b)>)" in report
    assert "核心判断" not in report
    assert "仍需回答的问题" not in report
    assert "尚未独立语义核验" not in report
    duplicate = answer()
    duplicate["sources"].append(copy.deepcopy(duplicate["sources"][0]))
    with pytest.raises(ResearchError):
        validate_draft(draft(duplicate), bundle)
    borrowed = answer()
    borrowed["sources"][0]["subjects"][0]["evidence"] = "E3"
    borrowed["sources"].append(empty)
    with pytest.raises(ResearchError):
        validate_draft(draft(borrowed), bundle)
    legacy = bundle.to_json()
    del legacy["sources"][0]["source"]["publisher"]
    old = compat.decode(BUNDLE, legacy, streamed=False)
    assert old.sources[0].source.publisher == ""


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "/outside.txt",
        "C:/private.txt",
        "dir\\file.txt",
        "CON.txt",
        "LPT1/report.txt",
        "directory./file.txt",
    ],
)
def test_bad_paths_fail_before_model(fixture: Fixture, path: str) -> None:
    fixture.edit(lambda v: v["sources"][0].__setitem__("original_file", path))
    with pytest.raises(ResearchError):
        prepare(fixture.input)


def test_bad_hash_and_ambiguous_quotes_fail_before_model(tmp_path: Path) -> None:
    f = Fixture(tmp_path / "a")
    f.edit(lambda v: v["sources"][0].__setitem__("sha256", "bad"))
    with pytest.raises(ResearchError, match="hash mismatch"):
        prepare(f.input)
    f = Fixture(tmp_path / "b")
    f.edit(lambda v: v["sources"][0]["excerpts"][0].__setitem__("quote", "。"))
    with pytest.raises(ResearchError, match="ambiguous"):
        prepare(f.input)
    f.edit(lambda v: v["sources"][0]["excerpts"][0].__setitem__("quote", "invented quote"))
    with pytest.raises(ResearchError):
        prepare(f.input)


def test_unknown_citation_discards_annotations_but_keeps_sources(fixture: Fixture) -> None:
    response = answer()
    response["sources"][0]["subjects"][0]["evidence"] = "invented"
    llm, _ = model(response)
    fixture.run(llm)
    assert fixture.count("SELECT count(*) FROM reports") == 1
    assert research.evidence(fixture.conn, fixture.root, TOPIC, PERIOD, "E1") is not None
    report = (fixture.root / "reports/research-test/2026-10-16-v1.md").read_text(encoding="utf-8")
    assert "**主体与职责线索**：未标注" in report
    assert "> 公司报告2025年开展8次培训。" in report
    assert "invented" not in report
    fingerprint = fixture.conn.execute("SELECT fingerprint FROM reports").fetchone()[0]
    assert "unknown evidence" in fingerprint
    with pytest.raises(ResearchError, match="already exists"):
        fixture.run(llm)
    assert fixture.count("SELECT count(*) FROM llm_calls") == 1


def test_extraction_discards_rewrites_and_rejects_missing_sources_and_judgment_fields(
    fixture: Fixture,
) -> None:
    bundle, _ = prepare(fixture.input)
    value = answer()
    value["sources"][0]["subjects"][0]["text"] = "企业获得融资改善"
    parsed = draft(value)
    assert validate_draft(parsed, bundle) == 1
    assert parsed.sources[0].subjects == []
    assert len(parsed.sources[0].objects) == 1
    assert "企业获得融资改善" not in render(bundle, parsed)
    value = answer()
    value["sources"] = []
    with pytest.raises(ResearchError):
        validate_draft(draft(value), bundle)
    for bad in ["", " "]:
        value = answer()
        value["sources"][0]["subjects"][0]["text"] = bad
        with pytest.raises(ResearchError):
            validate_draft(draft(value), bundle)
    for bad in ["<script>x</script>", "公司报告2025年开展9次培训。"]:
        value = answer()
        value["sources"][0]["subjects"][0]["text"] = bad
        parsed = draft(value)
        assert validate_draft(parsed, bundle) == 1
        assert bad not in render(bundle, parsed)
    for field in ["summary", "gaps", "inference", "recommendations", "score"]:
        value = answer()
        value[field] = "模型判断"
        with pytest.raises(compat.DecodeError):
            draft(value)
    oversized = answer()
    oversized["sources"][0]["subjects"] = [{"evidence": "E1", "text": "公司"}] * 9
    parsed = draft(oversized)
    assert validate_draft(parsed, bundle) == 9
    assert parsed.sources[0].subjects == []
    assert len(parsed.sources[0].objects) == 1
    assert "> 公司报告2025年开展8次培训。" in render(bundle, parsed)
    value = answer()
    value["sources"][0]["source_id"] = "unknown"
    with pytest.raises(ResearchError):
        validate_draft(draft(value), bundle)
    value = answer()
    value["sources"][0]["objects"] = [{"evidence": "E2", "text": "披露"}]
    parsed = draft(value)
    validate_draft(parsed, bundle)
    # A literal span can omit negation; it must never replace or rewrite the original sentence.
    assert "> 融资成本未披露。" in render(bundle, parsed)
    assert md("<script>x</script> [unsafe]") == "&lt;script&gt;x&lt;/script&gt; \\[unsafe\\]"


def test_actions_and_figures_keep_targets_as_source_text_without_progress_status(
    fixture: Fixture,
) -> None:
    goal = "力争到2026年覆盖100家企业，尚未完成年度目标。"
    raw = f"{RAW}{goal}"
    (fixture.root / "input" / "source.txt").write_text(raw, encoding="utf-8")

    def change(v: dict[str, Any]) -> None:
        v["sources"][0]["sha256"] = sha(raw)
        v["sources"][0]["excerpts"].append({"id": "E3", "location": "第3段", "quote": goal})

    fixture.edit(change)
    bundle, _ = prepare(fixture.input)
    for field in ["actions", "figures"]:
        value = answer()
        value["sources"][0][field] = [{"evidence": "E3", "text": goal}]
        parsed = draft(value)
        assert validate_draft(parsed, bundle) == 0
        report = render(bundle, parsed)
        link = f"[{goal}](/api/research/evidence?topic=research-test&period=2026-10-16-v1&id=E3)"
        assert link in report
        assert "**行动、要求与安排原文**" in report
        assert "**数据与指标原文**" in report
        assert "实际进展" not in report
        assert "已完成事项" not in report

        value["sources"][0][field][0]["text"] = "已覆盖100家企业"
        parsed = draft(value)
        assert validate_draft(parsed, bundle) == 1
        assert "已覆盖100家企业" not in render(bundle, parsed)
        assert f"> {goal}" in render(bundle, parsed)

        value["sources"][0][field][0]["evidence"] = "other-source-evidence"
        with pytest.raises(ResearchError):
            validate_draft(draft(value), bundle)
    judgment = answer()
    judgment["sources"][0]["reported_progress"] = [{"evidence": "E3", "text": goal}]
    with pytest.raises(compat.DecodeError):
        draft(judgment)


def test_tampered_frozen_content_and_other_workspaces_are_not_served(fixture: Fixture) -> None:
    llm, _ = model(answer())
    fixture.run(llm)
    other = db.connect(":memory:")
    assert research.evidence(other, fixture.root, TOPIC, PERIOD, "E1") is None
    assert research.original(fixture.conn, fixture.root, "../research-test", PERIOD, "S1") is None
    frozen = fixture.root / "research/research-test/2026-10-16-v1"
    (frozen / "S1.text.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(ResearchError):
        research.evidence(fixture.conn, fixture.root, TOPIC, PERIOD, "E1")
    (frozen / "S1.original.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(ResearchError):
        research.original(fixture.conn, fixture.root, TOPIC, PERIOD, "S1")
    (frozen / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ResearchError):
        research.evidence(fixture.conn, fixture.root, TOPIC, PERIOD, "E1")


def test_linked_inputs_and_oversized_files_are_rejected(fixture: Fixture) -> None:
    link = fixture.root / "input" / "link.txt"
    os.symlink(fixture.root / "input" / "source.txt", link)
    fixture.edit(lambda v: v["sources"][0].__setitem__("text_file", "link.txt"))
    with pytest.raises(ResearchError, match="links"):
        prepare(fixture.input)
    with pytest.raises(ResearchError, match="size limit"):
        read(fixture.root / "input" / "source.txt", 2)


# --- ownership, failed calls and error messages --------------------------------------


def test_report_file_of_another_kind_is_never_overwritten(fixture: Fixture) -> None:
    taken = fixture.root / "reports" / TOPIC / f"{PERIOD}.md"
    taken.parent.mkdir(parents=True)
    taken.write_text("# imported\n", encoding="utf-8")
    llm, seen = model(answer())
    with pytest.raises(ResearchError, match="^this report file already exists; choose a new period$"):
        fixture.run(llm)
    assert taken.read_text(encoding="utf-8") == "# imported\n"
    assert seen == []
    assert not (fixture.root / "research").exists()


def test_period_registered_by_any_kind_is_refused(fixture: Fixture) -> None:
    fixture.conn.execute(
        "INSERT INTO reports VALUES (?, ?, ?, 'weekly', ?, '{}', '2026-10-16')",
        (f"{TOPIC}/{PERIOD}/weekly", TOPIC, PERIOD, f"reports/{TOPIC}/{PERIOD}.md"),
    )
    llm, seen = model(answer())
    with pytest.raises(ResearchError, match="^this report period already exists"):
        fixture.run(llm)
    assert seen == []


def test_failed_model_call_keeps_the_reserved_bundle(fixture: Fixture) -> None:
    env = {"ISBE_LLM_PROVIDER": "openai", "ISBE_LLM_BASE_URL": "http://127.0.0.1:9/v1",
           "ISBE_LLM_NO_AUTH": "true"}
    client = http.Http(5, transport=httpx.MockTransport(lambda r: httpx.Response(400)))
    with pytest.raises(LlmError):
        fixture.run(Llm.from_vars(env.get, client=client))
    frozen = fixture.root / "research" / TOPIC / PERIOD
    assert sorted(p.name for p in frozen.iterdir()) == [
        "S1.original.txt", "S1.text.txt", "input.json", "manifest.json",
    ]
    assert not (fixture.root / "reports" / TOPIC / f"{PERIOD}.md").exists()
    llm, seen = model(answer())
    with pytest.raises(ResearchError) as error:
        fixture.run(llm)
    assert errors.describe(error.value) == (
        "research period already exists or cannot be reserved: File exists (os error 17)"
    )
    assert seen == []
    row = fixture.conn.execute("SELECT error FROM runs ORDER BY started_at DESC").fetchone()
    assert row[0].startswith("research period already exists")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda v: v["sources"][0].__setitem__("original_file", "missing.txt"),
         "read research file: No such file or directory (os error 2)"),
        (lambda v: v["sources"][0].__setitem__("original_file", "a\0b.txt"),
         "file name contained an unexpected NUL byte"),
        (lambda v: v["scope"].__setitem__("from", "2024-02-30"), "input is out of range"),
        (lambda v: v["scope"].__setitem__("from", "2027-01-01"), "research window is reversed"),
        (lambda v: v["sources"][0].__setitem__("published_on", "2026-13-01"),
         "invalid publication date: input is out of range"),
        (lambda v: v["sources"][0].__setitem__("url", "example.com"),
         "source URL must be HTTP(S) without credentials"),
        (lambda v: v["sources"][0].__setitem__("url", "example.com/path"),
         "invalid source URL: invalid format"),
        (lambda v: v["sources"][0].__setitem__("url", "https://u@x/"),
         "source URL must be HTTP(S) without credentials"),
        (lambda v: v.__setitem__("extra", 1),
         "invalid research input JSON: unknown field `extra`, expected one of `topic`, `title`, "
         "`period`, `scope`, `search_note`, `limitations`, `sources`"),
        (lambda v: v.__setitem__("topic", "con"), "invalid research topic or period"),
        (lambda v: v.__setitem__("title", "a\u0007b"), "unsupported control character"),
        (lambda v: v.__setitem__("title", "长" * 101), "empty or oversized research text"),
        (lambda v: v["scope"].__setitem__("languages", ["zh"] * 9), "too many research entries"),
        (lambda v: v["sources"].append(copy.deepcopy(v["sources"][0])),
         "invalid or repeated source id"),
        (lambda v: v["sources"][0]["excerpts"][1].__setitem__("id", "E1"),
         "invalid or repeated evidence id"),
    ],
)
def test_preparation_error_messages(
    fixture: Fixture, change: Any, message: str
) -> None:
    fixture.edit(change)
    with pytest.raises(ValueError) as error:
        prepare(fixture.input)
    assert errors.describe(error.value) == message


def test_seq_form_and_whitespace_replies_are_json(fixture: Fixture) -> None:
    llm, _ = model(None, raw='  {"sources":[["S1",[["E1","公司"]],[],[],[],[]]]}\n')
    stats = fixture.run(llm)
    assert stats["extraction_error"] is None
    report = (fixture.root / stats["report"]).read_text(encoding="utf-8")
    assert "**主体与职责线索**：[公司](/api/research/evidence?" in report


@pytest.mark.parametrize(
    "raw",
    ['```json\n{"sources":[]}\n```', '{"sources":[],"sources":[]}', "", "[]"],
)
def test_unusable_replies_keep_every_quotation(fixture: Fixture, raw: str) -> None:
    llm, _ = model(None, raw=raw)
    stats = fixture.run(llm)
    assert stats["extraction_error"] == "model research output must be JSON"
    assert stats["rejected_spans"] == 0
    report = (fixture.root / stats["report"]).read_text(encoding="utf-8")
    assert report.count("**原文内容**") == 1 and "> 融资成本未披露。" in report
