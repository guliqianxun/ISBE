import os
from dataclasses import dataclass
from functools import lru_cache

import httpx
from anthropic import Anthropic

ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-4-6"
DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Backward-compat alias (T13 spec name)
DEFAULT_MODEL = ANTHROPIC_DEFAULT_MODEL


# Langfuse decorator — graceful no-op if SDK not installed or keys not set
def _make_observe_decorator():
    try:
        from langfuse.decorators import observe

        return observe(as_type="generation")
    except ImportError:
        # langfuse not in deps for some reason — fall through to no-op
        def _noop(fn):
            return fn

        return _noop


_observe = _make_observe_decorator()


def _update_langfuse_observation(
    *, input_payload, output: str, model: str, input_tokens: int, output_tokens: int
) -> None:
    """Best-effort attach details to the current Langfuse observation; silent if no client."""
    try:
        from langfuse.decorators import langfuse_context

        langfuse_context.update_current_observation(
            input=input_payload,
            output=output,
            model=model,
            usage={"input": input_tokens, "output": output_tokens},
        )
    except Exception:
        pass  # langfuse not configured / not authenticated → silent skip


@dataclass(frozen=True)
class LLMResponse:
    text: str
    message_id: str
    input_tokens: int
    output_tokens: int
    trace_id: str | None


@lru_cache(maxsize=1)
def _get_anthropic_client() -> Anthropic:
    return Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))


def _complete_anthropic(
    system: str, user: str, model: str, max_tokens: int, trace_id: str | None
) -> LLMResponse:
    client = _get_anthropic_client()
    msg = client.messages.create(
        model=model,
        system=system,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in msg.content if hasattr(block, "text"))
    _update_langfuse_observation(
        input_payload={"system": system, "user": user},
        output=text,
        model=model,
        input_tokens=msg.usage.input_tokens,
        output_tokens=msg.usage.output_tokens,
    )
    return LLMResponse(
        text=text,
        message_id=msg.id,
        input_tokens=msg.usage.input_tokens,
        output_tokens=msg.usage.output_tokens,
        trace_id=trace_id,
    )


def _complete_deepseek(
    system: str, user: str, model: str, max_tokens: int, trace_id: str | None
) -> LLMResponse:
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is empty; set it in .env")
    resp = httpx.post(
        f"{DEEPSEEK_BASE_URL}/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
        },
        timeout=180.0,
    )
    resp.raise_for_status()
    data = resp.json()
    usage = data.get("usage", {})
    text = data["choices"][0]["message"]["content"]
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)
    _update_langfuse_observation(
        input_payload={"system": system, "user": user},
        output=text,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    return LLMResponse(
        text=text,
        message_id=data.get("id", "deepseek-unknown"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        trace_id=trace_id,
    )


@_observe
def complete(
    *,
    system: str,
    user: str,
    model: str | None = None,
    trace_id: str | None = None,
    max_tokens: int = 4096,
) -> LLMResponse:
    """Dispatch to anthropic or deepseek based on ISBE_LLM_PROVIDER env (default anthropic).

    Decorated with Langfuse @observe — when LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST are set,
    the call is traced as a 'generation' observation. Without keys, langfuse SDK warns
    once and degrades to no-op; this function still returns normally.
    """
    provider = os.getenv("ISBE_LLM_PROVIDER", "anthropic")
    if provider == "deepseek":
        return _complete_deepseek(
            system=system,
            user=user,
            model=model or DEEPSEEK_DEFAULT_MODEL,
            max_tokens=max_tokens,
            trace_id=trace_id,
        )
    return _complete_anthropic(
        system=system,
        user=user,
        model=model or ANTHROPIC_DEFAULT_MODEL,
        max_tokens=max_tokens,
        trace_id=trace_id,
    )
