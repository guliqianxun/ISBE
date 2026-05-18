import os
import time
from dataclasses import dataclass
from functools import lru_cache

import anthropic
import httpx
from anthropic import Anthropic
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

_RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
_RETRYABLE_HTTPX_EXC = (
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
    httpx.ConnectError,
)


def _is_retryable_llm_error(exc: BaseException) -> bool:
    if isinstance(exc, _RETRYABLE_HTTPX_EXC):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_HTTP_STATUSES
    # anthropic SDK exceptions
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code in _RETRYABLE_HTTP_STATUSES
    return False


def _sleep_between_attempts(seconds: float) -> None:
    """Test seam for tenacity's sleep callback."""
    time.sleep(seconds)


_LLM_RETRY_DECORATOR = retry(
    retry=retry_if_exception(_is_retryable_llm_error),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    sleep=_sleep_between_attempts,
    reraise=True,
)

ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-4-6"
DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Backward-compat alias (T13 spec name)
DEFAULT_MODEL = ANTHROPIC_DEFAULT_MODEL

# Tier defaults per provider. Override at runtime with ISBE_LLM_MODEL_FAST /
# ISBE_LLM_MODEL_SMART. DeepSeek has no smaller variant; both tiers fall back
# to deepseek-chat and the throughput win comes from batching, not model swap.
_TIER_DEFAULTS = {
    "anthropic": {
        "fast": "claude-haiku-4-5-20251001",
        "smart": ANTHROPIC_DEFAULT_MODEL,
    },
    "deepseek": {
        "fast": DEEPSEEK_DEFAULT_MODEL,
        "smart": DEEPSEEK_DEFAULT_MODEL,
    },
}


def _resolve_tier_model(provider: str, tier: str) -> str:
    if tier not in ("fast", "smart"):
        raise ValueError(f"tier must be 'fast' or 'smart', got {tier!r}")
    env_key = f"ISBE_LLM_MODEL_{tier.upper()}"
    override = os.getenv(env_key, "").strip()
    if override:
        return override
    return _TIER_DEFAULTS.get(provider, _TIER_DEFAULTS["anthropic"])[tier]


# Phoenix / OpenTelemetry tracer — best-effort init, no-op if collector unreachable
# or arize-phoenix-otel not installed. Controlled by PHOENIX_COLLECTOR_ENDPOINT env.
@lru_cache(maxsize=1)
def _get_tracer():
    try:
        from opentelemetry import trace

        endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "").strip()
        if endpoint:
            from phoenix.otel import register

            register(
                project_name=os.getenv("PHOENIX_PROJECT_NAME", "isbe"),
                endpoint=endpoint,
                set_global_tracer_provider=True,
                batch=True,
            )
        return trace.get_tracer("isbe.llm")
    except Exception:
        return None


def _set_llm_span_attrs(
    span, *, provider: str, model: str, system: str, user: str,
    output: str, input_tokens: int, output_tokens: int,
) -> None:
    """Best-effort OpenInference-flavored attributes; silent if span is None."""
    if span is None:
        return
    try:
        span.set_attribute("openinference.span.kind", "LLM")
        span.set_attribute("llm.provider", provider)
        span.set_attribute("llm.model_name", model)
        span.set_attribute("input.value", f"system: {system}\n\nuser: {user}")
        span.set_attribute("output.value", output)
        span.set_attribute("llm.token_count.prompt", int(input_tokens))
        span.set_attribute("llm.token_count.completion", int(output_tokens))
        span.set_attribute("llm.token_count.total", int(input_tokens + output_tokens))
    except Exception:
        pass


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


@_LLM_RETRY_DECORATOR
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
    return LLMResponse(
        text=text,
        message_id=msg.id,
        input_tokens=msg.usage.input_tokens,
        output_tokens=msg.usage.output_tokens,
        trace_id=trace_id,
    )


@_LLM_RETRY_DECORATOR
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
    return LLMResponse(
        text=text,
        message_id=data.get("id", "deepseek-unknown"),
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        trace_id=trace_id,
    )


def complete(
    *,
    system: str,
    user: str,
    model: str | None = None,
    tier: str = "smart",
    trace_id: str | None = None,
    max_tokens: int = 4096,
) -> LLMResponse:
    """Dispatch to anthropic or deepseek based on ISBE_LLM_PROVIDER env (default anthropic).

    `tier` selects the model class: "smart" for synthesis / heavy reasoning,
    "fast" for high-volume cheap calls (per-article reviews, batched extraction).
    Explicit `model=` always wins for back-compat with callers that pin a model.

    When PHOENIX_COLLECTOR_ENDPOINT is set, each call is emitted as an OpenInference-flavored
    LLM span to Phoenix. Without it, tracer is no-op and the call returns normally.
    """
    provider = os.getenv("ISBE_LLM_PROVIDER", "anthropic")
    resolved_model = model or _resolve_tier_model(provider, tier)

    tracer = _get_tracer()
    if tracer is None:
        if provider == "deepseek":
            return _complete_deepseek(system, user, resolved_model, max_tokens, trace_id)
        return _complete_anthropic(system, user, resolved_model, max_tokens, trace_id)

    with tracer.start_as_current_span(f"llm.complete.{provider}") as span:
        if provider == "deepseek":
            resp = _complete_deepseek(system, user, resolved_model, max_tokens, trace_id)
        else:
            resp = _complete_anthropic(system, user, resolved_model, max_tokens, trace_id)
        _set_llm_span_attrs(
            span,
            provider=provider,
            model=resolved_model,
            system=system,
            user=user,
            output=resp.text,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
        )
        return resp
