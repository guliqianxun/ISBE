from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from isbe.topics._shared.digester import (
    parse_distillation_section,
    weekly_digester,
)
from isbe.topics.base import DigestResult


def test_parse_distillation_yields_drafts():
    section = """- DRAFT[topics/nowcasting.theses.md]: 新论点：lead-time > 90min mode collapse
- DRAFT[reading/2026/W19/2604.12345.md]: PaperX 已自动标注

无前缀的行应忽略
"""
    drafts = parse_distillation_section(section)
    assert len(drafts) == 2
    assert drafts[0].target_path == "topics/nowcasting.theses.md"
    assert "lead-time" in drafts[0].body
    assert drafts[1].target_path.startswith("reading/")


def test_weekly_digester_end_to_end_mocked(memory_dir: Path, monkeypatch):
    """Mock papers query + LLM + artifact store; verify produces DigestResult + .pending."""
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))

    # Bootstrap minimum memory files
    (memory_dir / "topics" / "nowcasting.md").write_text(
        """---
name: nowcasting
description: nowcasting topic
type: topic
created: 2026-05-07
updated: 2026-05-07
source: user-edited
---
keywords: a, b
""",
        encoding="utf-8",
    )

    fake_papers = [
        MagicMock(
            arxiv_id="2604.12345",
            title="Precipitation Nowcasting with a New Diffusion Method",
            abstract="abstract",
            authors=["Alice"],
            primary_category="cs.LG",
            submitted_at=datetime(2026, 5, 1, tzinfo=UTC),
            source_url="https://arxiv.org/abs/2604.12345",
        )
    ]

    fake_session = MagicMock()
    fake_session.__enter__ = MagicMock(return_value=fake_session)
    fake_session.__exit__ = MagicMock(return_value=False)
    fake_session.scalars.return_value.all.return_value = fake_papers
    # No prior artifact in this test — comparison should be None.
    fake_session.scalars.return_value.first.return_value = None

    fake_llm_resp = MagicMock(text="""## TL;DR
- 本期 1 篇 / 1 篇值得读：PaperX
- 主进展：新方法

## 论文逐篇
### [2604.12345]
- 评价: PaperX 强相关，引入了新方法，值得细读。
- 速览: 用新方法把临近预报做得更准，代码开源。
- 来源: 第一作者 A. Researcher（Foo Lab）；本文自述延续 DGMR 路线。
- 方法: 扩散模型条件化雷达图；建立在 DGMR 之上。
- 数据: SEVIR（公开数据集）。
- 代码: https://github.com/example/paperx
- 复现: 开源=是 · 权重=未知 · 算力=训练 1×A100 · 代码完整度=中
- 效果: CSI@SEVIR vs DGMR: 0.40→0.45

## 仓库逐条
(本期无仓库)

## 名词
- CSI: 临近预报命中率指标，越高越准

## 分析
PaperX 提了新方法 (memory: nowcasting@1)。

## 蒸馏
- DRAFT[topics/nowcasting.theses.md]: 新论点候选
""", message_id="m1", input_tokens=100, output_tokens=50, trace_id="t1")

    fake_obs_session = MagicMock()
    fake_obs_session.__enter__ = MagicMock(return_value=fake_obs_session)
    fake_obs_session.__exit__ = MagicMock(return_value=False)

    captured: dict[str, str] = {}

    def _capture_save(**kwargs):
        captured["body"] = kwargs["body_markdown"]
        return "00000000-0000-0000-0000-000000000001"

    with patch("isbe.topics._shared.digester.make_session_factory",
               return_value=lambda: fake_session), \
         patch("isbe.topics._shared.digester.complete", return_value=fake_llm_resp), \
         patch("isbe.topics._shared.digester.save_artifact",
               side_effect=_capture_save), \
         patch("isbe.observability.runs.make_session_factory",
               return_value=lambda: fake_obs_session):
        result = weekly_digester(
            topic_id="nowcasting", period_label="2026-W19", today=date(2026, 5, 7)
        )

    assert isinstance(result, DigestResult)
    assert result.topic_id == "nowcasting"
    assert {s.kind for s in result.sections} == {
        "tldr", "paper_reviews", "repo_reviews", "analysis", "distillation",
    }
    assert len(result.pending_drafts) == 1
    pending_root = memory_dir / ".pending"
    assert any(p.suffix == ".md" for p in pending_root.rglob("*"))

    # Render-path coverage: the structured payload must actually reach the
    # artifact markdown (this guards the digester→template wiring + the v2
    # SOTA-table / glossary / repro-badge format, which is otherwise untested).
    body = captured["body"]
    assert "**本期评价**" in body
    # verifiability-first fields lead the card
    assert "**来源**" in body and "Foo Lab" in body
    assert "**方法/背景**" in body and "DGMR" in body
    assert "**数据**" in body and "SEVIR（公开数据集）" in body
    assert "**代码**" in body and "github.com/example/paperx" in body
    assert "**复现**" in body and "开源=是" in body
    # effect numbers are a demoted reference, not the headline
    assert "效果（参考指标，非排名结论）" in body
    assert "| 指标 | 数据集 | 对照基线 | 基线 | 新值 | Δ |" in body
    assert "+12.5%" in body  # 0.40→0.45 computed delta
    assert "速览（入门）" in body
    assert "## 名词（入门）" in body and "| CSI |" in body
    assert '<details markdown="1"><summary>摘要原文</summary>' in body
