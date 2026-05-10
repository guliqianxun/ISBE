"""End-to-end mock test for the NVDA daily digester."""
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from isbe.topics.base import DigestResult
from isbe.topics.nvda.digester import daily_digester


def test_daily_digester_end_to_end_mocked(memory_dir: Path, monkeypatch):
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))

    (memory_dir / "topics" / "nvda.md").write_text(
        """---
name: nvda
description: nvda topic
type: topic
created: 2026-05-10
updated: 2026-05-10
source: user-edited
---
NVDA scope.
""",
        encoding="utf-8",
    )

    fake_price = MagicMock(symbol="NVDA", trade_date=date(2026, 5, 10),
                           close=1234.56, volume=10_000_000, adj_close=1234.56)
    fake_news = MagicMock(source="reuters", published_at=datetime(2026, 5, 10, 21, tzinfo=UTC),
                          headline="NVIDIA records", url="https://x", tickers=["NVDA"])
    fake_filing = MagicMock(form_type="8-K", filed_at=datetime(2026, 5, 10, tzinfo=UTC),
                            ticker="NVDA", body_url="https://sec.gov/x")

    fake_session = MagicMock()
    fake_session.__enter__ = MagicMock(return_value=fake_session)
    fake_session.__exit__ = MagicMock(return_value=False)
    fake_session.scalars.side_effect = [
        MagicMock(all=MagicMock(return_value=[fake_price])),
        MagicMock(all=MagicMock(return_value=[fake_news])),
        MagicMock(all=MagicMock(return_value=[fake_filing])),
    ]

    fake_llm_resp = MagicMock(text="""## 事实
NVDA close $1234.56.

## 分析
归因：大盘主导 (memory: nvda@1)。

## 蒸馏
- DRAFT[topics/nvda.theses.md]: 新论点候选
""", message_id="m1", input_tokens=120, output_tokens=60, trace_id="t1")

    fake_obs = MagicMock()
    fake_obs.__enter__ = MagicMock(return_value=fake_obs)
    fake_obs.__exit__ = MagicMock(return_value=False)

    with patch("isbe.topics.nvda.digester.make_session_factory",
               return_value=lambda: fake_session), \
         patch("isbe.topics.nvda.digester.complete", return_value=fake_llm_resp), \
         patch("isbe.topics.nvda.digester.save_artifact",
               return_value="00000000-0000-0000-0000-000000000010"), \
         patch("isbe.observability.runs.make_session_factory",
               return_value=lambda: fake_obs):
        result = daily_digester(period_label="2026-05-10", today=date(2026, 5, 10))

    assert isinstance(result, DigestResult)
    assert result.topic_id == "nvda"
    assert {s.kind for s in result.sections} == {"facts", "analysis", "distillation"}
    assert len(result.pending_drafts) == 1
