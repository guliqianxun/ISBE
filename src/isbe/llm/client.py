import os
from dataclasses import dataclass
from functools import lru_cache

from anthropic import Anthropic

DEFAULT_MODEL = "claude-sonnet-4-6"


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


def complete(
    *, system: str, user: str, model: str = DEFAULT_MODEL, trace_id: str | None = None,
    max_tokens: int = 4096,
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
