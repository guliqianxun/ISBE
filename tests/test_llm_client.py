from unittest.mock import MagicMock, patch

from isbe.llm.client import LLMResponse, complete
from isbe.llm.prompts import build_digest_prompt


def test_build_digest_prompt_includes_facts_and_memory():
    prompt = build_digest_prompt(
        topic_label="Nowcasting",
        period_label="2026-W19",
        facts_block="paper1 / paper2",
        memory_block="topic@rev1: keywords=...",
    )
    assert "Nowcasting" in prompt
    assert "2026-W19" in prompt
    assert "paper1" in prompt
    assert "topic@rev1" in prompt
    # 三段约定
    assert "事实" in prompt or "facts" in prompt.lower()
    assert "分析" in prompt or "analysis" in prompt.lower()
    assert "蒸馏" in prompt or "distillation" in prompt.lower()


def test_complete_calls_anthropic_and_returns_text():
    fake_msg = MagicMock()
    fake_msg.content = [MagicMock(text="generated text")]
    fake_msg.id = "msg_123"
    fake_msg.usage = MagicMock(input_tokens=10, output_tokens=5)
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_msg
    with patch("isbe.llm.client._get_anthropic_client", return_value=fake_client):
        resp = complete(
            system="sys prompt", user="user prompt",
            model="claude-sonnet-4-6", trace_id="t1",
        )
    assert isinstance(resp, LLMResponse)
    assert resp.text == "generated text"
    assert resp.message_id == "msg_123"
    assert resp.input_tokens == 10
    fake_client.messages.create.assert_called_once()
