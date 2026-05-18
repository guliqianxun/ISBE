"""Model-tier routing in llm.client.complete().

The new `tier` kwarg picks a fast vs smart model so big jobs (per-article
review generation, hundreds of items) can run on a cheaper model while
synthesis stays on the smart one. Env vars override defaults.
"""

from __future__ import annotations

import pytest

from isbe.llm.client import complete


def _fake_resp(text="ok", model_used=None):
    from isbe.llm.client import LLMResponse

    return LLMResponse(text=text, message_id="m", input_tokens=1, output_tokens=1, trace_id=None)


@pytest.fixture
def force_anthropic(monkeypatch):
    monkeypatch.setenv("ISBE_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub")


@pytest.fixture
def force_deepseek(monkeypatch):
    monkeypatch.setenv("ISBE_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "stub")


def _captured(monkeypatch):
    """Patch the provider-specific impl and return a dict that captures the model arg."""
    seen: dict = {}

    def fake_anth(system, user, model, max_tokens, trace_id):
        seen["model"] = model
        return _fake_resp()

    def fake_ds(system, user, model, max_tokens, trace_id):
        seen["model"] = model
        return _fake_resp()

    monkeypatch.setattr("isbe.llm.client._complete_anthropic", fake_anth)
    monkeypatch.setattr("isbe.llm.client._complete_deepseek", fake_ds)
    return seen


def test_anthropic_smart_tier_defaults_to_sonnet(monkeypatch, force_anthropic):
    seen = _captured(monkeypatch)
    complete(system="s", user="u", tier="smart")
    assert "sonnet" in seen["model"].lower()


def test_anthropic_fast_tier_defaults_to_haiku(monkeypatch, force_anthropic):
    seen = _captured(monkeypatch)
    complete(system="s", user="u", tier="fast")
    assert "haiku" in seen["model"].lower()


def test_default_tier_is_smart_back_compat(monkeypatch, force_anthropic):
    """Existing call sites pass no tier — must keep getting the smart model."""
    seen = _captured(monkeypatch)
    complete(system="s", user="u")  # no tier kwarg
    assert "sonnet" in seen["model"].lower()


def test_deepseek_uses_chat_for_both_tiers_by_default(monkeypatch, force_deepseek):
    seen = _captured(monkeypatch)
    complete(system="s", user="u", tier="fast")
    fast_model = seen["model"]
    complete(system="s", user="u", tier="smart")
    smart_model = seen["model"]
    # DeepSeek currently has no cheaper variant — both tiers fall back to the
    # provider default. Tiering on DeepSeek is mainly about batching, not models.
    assert "deepseek-chat" in fast_model
    assert "deepseek-chat" in smart_model


def test_env_override_for_fast_tier(monkeypatch, force_anthropic):
    monkeypatch.setenv("ISBE_LLM_MODEL_FAST", "my-custom-fast-model")
    seen = _captured(monkeypatch)
    complete(system="s", user="u", tier="fast")
    assert seen["model"] == "my-custom-fast-model"


def test_env_override_for_smart_tier(monkeypatch, force_anthropic):
    monkeypatch.setenv("ISBE_LLM_MODEL_SMART", "my-custom-smart-model")
    seen = _captured(monkeypatch)
    complete(system="s", user="u", tier="smart")
    assert seen["model"] == "my-custom-smart-model"


def test_explicit_model_arg_beats_tier(monkeypatch, force_anthropic):
    """Existing callers that pass model=... must still win — back-compat seam."""
    seen = _captured(monkeypatch)
    complete(system="s", user="u", model="explicit-model", tier="fast")
    assert seen["model"] == "explicit-model"


def test_unknown_tier_raises(monkeypatch, force_anthropic):
    with pytest.raises(ValueError, match="tier"):
        complete(system="s", user="u", tier="ultra")  # type: ignore[arg-type]
