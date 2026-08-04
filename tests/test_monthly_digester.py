"""Tests for the monthly rollup digester."""

from unittest.mock import MagicMock, patch

import pytest

from isbe.topics._shared import monthly as monthly_mod
from isbe.topics._shared.monthly import (
    _split_sections,
    gather_weekly_bodies,
    iso_weeks_of_month,
    monthly_digester,
)


def test_iso_weeks_of_month_thursday_rule():
    # Aug 2026: Thursdays are 6/13/20/27 → W32-W35. W31's Thursday (Jul 30)
    # belongs to July.
    assert iso_weeks_of_month(2026, 8) == ["2026-W32", "2026-W33", "2026-W34", "2026-W35"]
    assert "2026-W31" in iso_weeks_of_month(2026, 7)


def test_gather_weekly_bodies_strips_images_and_caps(monkeypatch, tmp_path):
    monkeypatch.setenv("ISBE_ARTIFACT_MIRROR", str(tmp_path))
    d = tmp_path / "nowcasting" / "2026-W32"
    d.mkdir(parents=True)
    body = "# 周报\n\n![fig](data:image/png;base64,AAAA)\n\n" + "正文" * 20_000
    (d / "latest.md").write_text(body, encoding="utf-8")

    out = gather_weekly_bodies("nowcasting", ["2026-W32", "2026-W33"])
    assert len(out) == 1  # missing week silently skipped
    assert out[0]["label"] == "2026-W32"
    assert "base64" not in out[0]["body"]
    assert "[图]" in out[0]["body"]
    assert len(out[0]["body"]) <= 18_000


def test_split_sections_generic():
    text = "## 月度总览\nA\n\n## 本月必读\n- [1.1] x\n\n## 蒸馏\n(本月无蒸馏建议)\n"
    parts = _split_sections(text)
    assert parts["月度总览"] == "A"
    assert parts["本月必读"].startswith("- [1.1]")
    assert parts["蒸馏"] == "(本月无蒸馏建议)"


_LLM_OUTPUT = """## 月度总览
- 主线：flow 非自回归路线升温

## 本月必读
- [2608.01626] QWRF-Net — 多尺度+flow 的代表作

## 论点演化
证据加强了 (memory: nowcasting.theses@rev2) 论点 1。

## 下月关注
- NeurIPS 截稿后的 nowcasting 投稿潮

## 蒸馏
- DRAFT[topics/nowcasting.theses.md]: 月度确认：flow 非自回归是 lead-time 主线
"""


@pytest.fixture(autouse=True)
def _no_run_persistence():
    with patch("isbe.observability.runs._persist_run"):
        yield


def _fake_resp(text: str):
    r = MagicMock()
    r.text = text
    r.trace_id = "trace123"
    r.input_tokens = 1000
    r.output_tokens = 400
    return r


def test_monthly_flow_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setenv("ISBE_ARTIFACT_MIRROR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(tmp_path / "memory"))
    (tmp_path / "memory").mkdir()
    wk = tmp_path / "artifacts" / "nowcasting" / "2026-W32"
    wk.mkdir(parents=True)
    (wk / "latest.md").write_text("# 周报 W32\n\n- [2608.01626] QWRF-Net", encoding="utf-8")

    saved = {}

    def fake_save(**kwargs):
        saved.update(kwargs)
        return kwargs["artifact_id"]

    with (
        patch.object(monthly_mod, "complete", return_value=_fake_resp(_LLM_OUTPUT)),
        patch.object(monthly_mod, "save_artifact", side_effect=fake_save),
        patch.object(monthly_mod, "send_digest_notification", return_value=True) as notify,
        patch.object(
            monthly_mod, "load_topic_config", return_value={"label": "临近降水预报科研订阅"}
        ),
    ):
        result = monthly_digester("nowcasting", month_label="2026-08")

    assert result.period_label == "2026-08"
    assert len(result.pending_drafts) == 1
    assert saved["kind"] == "monthly_digest"
    assert "QWRF-Net" in saved["body_markdown"]
    assert "论点演化" in saved["body_markdown"]
    assert str(saved["artifact_id"]) in saved["body_markdown"]  # no placeholder
    assert (tmp_path / "memory" / ".pending" / "topics" / "nowcasting.theses.md").exists()
    notify.assert_called_once()
    assert "月报" in notify.call_args.kwargs["topic_label"]


def test_monthly_flow_no_weeklies_is_clean_noop(monkeypatch, tmp_path):
    monkeypatch.setenv("ISBE_ARTIFACT_MIRROR", str(tmp_path / "artifacts"))
    with (
        patch.object(monthly_mod, "complete") as llm,
        patch.object(monthly_mod, "load_topic_config", return_value={"label": "t"}),
    ):
        result = monthly_digester("nowcasting", month_label="2026-08")
    llm.assert_not_called()
    assert result.pending_drafts == []


def test_monthly_default_label_is_previous_month(monkeypatch, tmp_path):
    from datetime import date

    monkeypatch.setenv("ISBE_ARTIFACT_MIRROR", str(tmp_path / "artifacts"))
    with (
        patch.object(monthly_mod, "complete") as llm,
        patch.object(monthly_mod, "load_topic_config", return_value={"label": "t"}),
    ):
        result = monthly_digester("nowcasting", today=date(2026, 9, 1))
    llm.assert_not_called()  # no weeklies in tmp mirror
    assert result.period_label == "2026-08"


def test_dispatch_resolves_monthly_digester():
    from isbe.topics.dispatch import resolve_flow

    flow, params = resolve_flow("nowcasting", "monthly_digester")
    assert flow.name == "monthly-digester"
    assert params == {"topic_id": "nowcasting"}
