from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from isbe.topics.base import DigestResult
from isbe.topics.nowcasting.digester import (
    parse_distillation_section,
    weekly_digester,
)


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
            title="Test paper",
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

    fake_llm_resp = MagicMock(text="""## 事实
当周期 1 篇论文。

## 分析
PaperX 提了新方法 (memory: nowcasting@1)。

## 蒸馏
- DRAFT[topics/nowcasting.theses.md]: 新论点候选
""", message_id="m1", input_tokens=100, output_tokens=50, trace_id="t1")

    with patch("isbe.topics.nowcasting.digester.make_session_factory",
               return_value=lambda: fake_session), \
         patch("isbe.topics.nowcasting.digester.complete", return_value=fake_llm_resp), \
         patch("isbe.topics.nowcasting.digester.save_artifact",
               return_value="00000000-0000-0000-0000-000000000001"):
        result = weekly_digester(period_label="2026-W19", today=date(2026, 5, 7))

    assert isinstance(result, DigestResult)
    assert result.topic_id == "nowcasting"
    assert {s.kind for s in result.sections} == {"facts", "analysis", "distillation"}
    assert len(result.pending_drafts) == 1
    pending_root = memory_dir / ".pending"
    assert any(p.suffix == ".md" for p in pending_root.rglob("*"))
