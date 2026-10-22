"""LLM calls. Every call goes through `Llm.complete`, is retried on
transient failures and is logged to `llm_calls` (its only writer). Providers:
the Anthropic Messages API (default), DeepSeek's chat API and any
OpenAI-compatible chat API. Plain HTTP, no vendor SDK, so requests and logs
are exactly what this module builds.

Outcomes a caller must handle:
- `LlmError` from a failed call (HTTP status, network, malformed reply). Its
  cause chain (`errors.describe`) is safe to store and show; provider bodies
  are never included.
- A request the server rejects because prompt + max_tokens exceed its context
  is sent once more with the output the context leaves (less a small margin),
  when the rejection names both counts (Strata's "request exceeds the context
  window" 400).
  Otherwise, or if that fails too, it is an `LlmError`.
- A reply cut off at its token limit is logged, then raised as `LlmError`
  ("... hit max_tokens (N); see llm_calls <id>", noting a limit lowered to fit
  the context).
- A refusal — Anthropic `stop_reason: "refusal"`, OpenAI `finish_reason:
  "content_filter"` — is logged, then raised as `Refused`, which carries the
  logged `Reply` (`reply.stop`, `reply.call_id`, any partial text). It is not
  an ordinary answer: triage must not cache a verdict from
  it (treat the batch as failed so it is judged again later), and report
  stages must fail rather than publish partial text.
- Otherwise `Reply.stop` holds the provider's stop reason for callers that
  want it (`end_turn`, `stop`, ...).
"""

import logging
import re
import sqlite3
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

from isbe.core import db, http, jsonfmt, settings, validators
from isbe.core.validators import RESERVED_BODY, provider_defaults

log = logging.getLogger(__name__)

__all__ = ["Llm", "LlmError", "Prompt", "Refused", "Reply", "Tier", "provider_defaults"]

_RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504, 529})
_ERROR_BODY = 16 * 1024
_REFUSALS = frozenset({"refusal", "content_filter"})
_U32_MAX = 0xFFFFFFFF
_CONTEXT = re.compile(
    r"request exceeds the context window: prompt \((\d{1,9}) tokens\) "
    r"\+ max tokens \(\d{1,9}\) exceeds the context \((\d{1,9})\)"
)
# Kept free of the fitted output: Strata refuses prompt + max_tokens + 8 > context.
_CONTEXT_SLACK = 64


class Tier(Enum):
    """Fast = high-volume cheap calls (triage judge); smart = the report."""

    FAST = "fast"
    SMART = "smart"


@dataclass(frozen=True)
class Prompt:
    purpose: str  # `llm_calls.purpose`: judge | digest | ...
    tier: Tier
    system: str
    user: str
    max_tokens: int


@dataclass(frozen=True)
class Reply:
    text: str
    call_id: str  # `llm_calls.id`, for report fingerprints
    stop: str | None = None  # stop_reason / finish_reason as the provider sent it


class LlmError(Exception):
    pass


class Refused(LlmError):
    """The model refused or its output was filtered; the call is logged as `reply.call_id`."""

    def __init__(self, message: str, reply: Reply) -> None:
        super().__init__(message)
        self.reply = reply


class ProviderError(Exception):
    """A non-200 answer, described only by its status and a fixed message."""


class ContextExceeded(ProviderError):
    """The observed context rejection, with the prompt and context sizes as the
    server counted them (None when its message did not give both)."""

    def __init__(self, prompt: int | None, context: int | None) -> None:
        super().__init__(
            "HTTP 400: input and requested output exceed the model context window; "
            "reduce input or maximum output tokens"
        )
        self.prompt, self.context = prompt, context


def _debug(value: str) -> str:
    """A string quoted and escaped for the provider error message."""
    out = []
    for c in value:
        match c:
            case "\\" | '"':
                out.append("\\" + c)
            case "\n":
                out.append("\\n")
            case "\r":
                out.append("\\r")
            case "\t":
                out.append("\\t")
            case "\0":
                out.append("\\0")
            case _ if not c.isprintable():
                out.append(f"\\u{{{ord(c):x}}}")
            case _:
                out.append(c)
    return '"' + "".join(out) + '"'


def _local(base: str | None) -> bool:
    uri = validators.http_uri(base) if base else None
    if uri is None:
        return False
    host = uri.host.strip("[]")
    if host.lower() == "localhost":
        return True
    ip = validators.parse_ip(host)
    if ip is None:
        return False
    if ip.version == 4:
        a, b = ip.packed[0], ip.packed[1]
        return a in (10, 127) or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)
    return ip.is_loopback or (int(ip) >> 121) == 0x7E  # ::1 or unique local fc00::/7


@dataclass
class Llm:
    provider: str
    base: str
    fast: str
    smart: str
    # `ISBE_LLM_MAX_TOKENS`: a ceiling on every call's output, for servers whose
    # context cannot hold prompt + the default output budget and whose rejection
    # does not name the context size (`complete` fits the ones that do).
    cap: int
    # `ISBE_LLM_EXTRA_BODY`: fields merged into every request, e.g.
    # {"chat_template_kwargs": {"enable_thinking": false}} for Qwen on vLLM.
    extra: dict[str, Any]
    http: http.Http
    _key: str = field(default="", repr=False)
    _sleep: Callable[[float], None] = field(default=time.sleep, repr=False)

    @classmethod
    def from_env(cls, *, client: http.Http | None = None) -> Llm:
        """From the effective settings (`settings.get`)."""
        return cls.from_vars(settings.get, client=client)

    @classmethod
    def from_vars(
        cls, env: Callable[[str], str | None], *, client: http.Http | None = None
    ) -> Llm:
        """`ISBE_LLM_PROVIDER` = anthropic (default) | deepseek | openai, with its key in
        `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`; the model, base
        URL, token cap, extra body and timeout settings override the defaults.
        `client` replaces the HTTP client (tests). Sends no request.
        """

        def var(name: str) -> str | None:
            value = env(name)
            value = validators.trim(value) if value is not None else None
            return value or None

        name = var("ISBE_LLM_PROVIDER") or "anthropic"
        for checked in (
            "ISBE_LLM_BASE_URL",
            "ISBE_LLM_MAX_TOKENS",
            "ISBE_LLM_TIMEOUT_S",
            "ISBE_LLM_EXTRA_BODY",
            "ISBE_LLM_NO_AUTH",
        ):
            settings.validate(checked, var(checked))
        keys = {"anthropic": "ANTHROPIC_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}
        keys["openai"] = "OPENAI_API_KEY"
        if name not in keys:
            raise LlmError(
                f"ISBE_LLM_PROVIDER={_debug(name)}: expected anthropic, deepseek or openai"
            )
        key_var = keys[name]
        base, fast, smart = provider_defaults(name)
        no_auth = name == "openai" and var("ISBE_LLM_NO_AUTH") == "true"
        if no_auth and not _local(var("ISBE_LLM_BASE_URL")):
            raise LlmError(
                "ISBE_LLM_NO_AUTH requires an explicit local ISBE_LLM_BASE_URL "
                "(localhost, loopback or private IP)"
            )
        key = "" if no_auth else var(key_var)
        if key is None:
            raise LlmError(
                f"{key_var} is not set (ISBE_LLM_PROVIDER={name}); local OpenAI-compatible "
                "services may explicitly enable ISBE_LLM_NO_AUTH"
            )
        cap = var("ISBE_LLM_MAX_TOKENS")
        extra = var("ISBE_LLM_EXTRA_BODY")
        timeout = var("ISBE_LLM_TIMEOUT_S")
        return cls(
            provider=name,
            base=(var("ISBE_LLM_BASE_URL") or base).rstrip("/"),
            fast=var("ISBE_LLM_MODEL_FAST") or fast,
            smart=var("ISBE_LLM_MODEL_SMART") or smart,
            cap=_U32_MAX if cap is None else int(cap),
            extra={} if extra is None else jsonfmt.loads(extra),
            # Nothing streams, and a long report takes minutes to write; a slow
            # local model needs longer.
            http=client or http.Http(settings.LLM_TIMEOUT if timeout is None else int(timeout)),
            _key=key,
        )

    def complete(self, conn: sqlite3.Connection, run_id: str, p: Prompt) -> Reply:
        """One completion, logged against `run_id` (see the module docstring for outcomes)."""
        model = self.fast if p.tier is Tier.FAST else self.smart
        max_tokens = min(p.max_tokens, self.cap)
        fitted = False
        started = time.monotonic()
        try:
            try:
                response = self.send(model, p, max_tokens)
            except ContextExceeded as rejected:
                # The output budgets are ceilings, not needs (a report is a few
                # thousand tokens of its 24k): give the output what the context
                # leaves this prompt, once.
                if rejected.prompt is None or rejected.context is None:
                    raise
                room = rejected.context - rejected.prompt - _CONTEXT_SLACK
                if not 0 < room < max_tokens:
                    raise
                log.warning(
                    "llm: %s call to %s: prompt (%s tokens) + max_tokens (%s) exceed the "
                    "model context (%s); retrying with max_tokens %s",
                    p.purpose, model, rejected.prompt, max_tokens, rejected.context, room,
                )
                max_tokens, fitted = room, True
                started = time.monotonic()
                response = self.send(model, p, max_tokens)
        except Exception as error:
            raise LlmError(f"{p.purpose} call to {model}") from error
        ms = int((time.monotonic() - started) * 1000)
        try:
            parse = parse_anthropic if self.provider == "anthropic" else parse_openai
            text, tokens_in, tokens_out, stop = parse(response)
        except Exception as error:
            raise LlmError(f"{p.purpose} reply from {model}") from error
        created = db.now()
        prompt = jsonfmt.dumps({"system": p.system, "user": p.user, "max_tokens": max_tokens})
        prompt_sha = db.sha256(prompt)
        call_id = f"{created}/{p.purpose}/{prompt_sha[:12]}"
        conn.execute(
            "INSERT INTO llm_calls (id, run_id, purpose, model, prompt_sha, prompt, response, "
            "tokens_in, tokens_out, ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (call_id, run_id, p.purpose, model, prompt_sha, prompt, text, tokens_in, tokens_out,
             ms, created),
        )
        reply = Reply(text, call_id, stop)
        if stop == ("max_tokens" if self.provider == "anthropic" else "length"):
            lowered = ", lowered to fit the model context" if fitted else ""
            raise LlmError(
                f"{p.purpose} reply hit max_tokens ({max_tokens}{lowered}); see llm_calls {call_id}"
            )
        if stop in _REFUSALS:
            raise Refused(
                f"{p.purpose} reply was refused by the model ({stop}); see llm_calls {call_id}",
                reply,
            )
        return reply

    def _request(self, model: str, p: Prompt, max_tokens: int) -> tuple[str, dict[str, Any]]:
        if self.provider == "anthropic":
            return f"{self.base}/v1/messages", {
                "model": model,
                "max_tokens": max_tokens,
                "system": p.system,
                "messages": [{"role": "user", "content": p.user}],
            }
        messages = [{"role": "system", "content": p.system}, {"role": "user", "content": p.user}]
        if self.provider == "deepseek":
            # deepseek-chat rejects max_tokens above 8192.
            return f"{self.base}/chat/completions", {
                "model": model, "max_tokens": min(max_tokens, 8192), "messages": messages,
            }
        # Reasoning models refuse max_tokens; the cap includes their reasoning.
        return f"{self.base}/chat/completions", {
            "model": model, "max_completion_tokens": max_tokens, "messages": messages,
        }

    def send(self, model: str, p: Prompt, max_tokens: int) -> Any:
        """POST with up to two retries (after 5 s, then 30 s) on rate limits, overload,
        5xx and network errors; not on a timeout, which would just repeat."""
        url, body = self._request(model, p, max_tokens)
        # Validation already refuses reserved keys; never let one replace ours anyway.
        body.update((k, v) for k, v in self.extra.items() if k not in RESERVED_BODY)
        if self.provider == "anthropic":
            headers = {"x-api-key": self._key, "anthropic-version": "2023-06-01"}
        else:
            headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        waits = iter((5, 30))
        while True:
            error: Exception
            with ExitStack() as stack:
                try:
                    response = stack.enter_context(
                        self.http.stream("POST", url, json=body, headers=headers)
                    )
                except httpx.TimeoutException:
                    raise
                except (httpx.NetworkError, httpx.RemoteProtocolError) as failure:
                    retry, error = True, failure
                else:
                    status = response.status_code
                    if status == 200:
                        return jsonfmt.loads(http.read_capped(response, http.MAX_BODY))
                    retry = status in _RETRY_STATUS
                    error = _rejection(status, response)
            wait = next(waits, None)
            if not retry or wait is None:
                raise error
            log.warning("llm: %s; retrying in %ss", error, wait)
            self._sleep(wait)


def _rejection(status: int, response: httpx.Response) -> ProviderError:
    """Only classify the observed context rejection; never expose provider text
    (its two counts are read as numbers, for `complete` to fit the output)."""
    if status == 400:
        try:
            detail = jsonfmt.loads(http.read_capped(response, _ERROR_BODY))
        except (ValueError, http.BodyTooLarge, httpx.HTTPError):
            detail = None
        error = detail.get("error") if isinstance(detail, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        if (
            isinstance(error, dict)
            and error.get("type") == "invalid_request_error"
            and isinstance(message, str)
            and message.startswith("request exceeds the context window: prompt (")
        ):
            counts = _CONTEXT.match(message)
            if counts is None:
                return ContextExceeded(None, None)
            return ContextExceeded(int(counts[1]), int(counts[2]))
    return ProviderError(f"HTTP {status}: model provider rejected the request")


def _i64(value: Any) -> int | None:
    """Integers only, within the signed 64-bit range."""
    if isinstance(value, int) and not isinstance(value, bool) and -(1 << 63) <= value < (1 << 63):
        return value
    return None


def _get(value: Any, *keys: Any) -> Any:
    """`value[k1][k2]...`, or None for anything missing."""
    for key in keys:
        if isinstance(key, int):
            value = value[key] if isinstance(value, list) and len(value) > key else None
        else:
            value = value.get(key) if isinstance(value, dict) else None
    return value


type Parsed = tuple[str, int | None, int | None, str | None]


def parse_anthropic(v: Any) -> Parsed:
    """Text blocks joined, token counts and `stop_reason`."""
    blocks = _get(v, "content")
    if not isinstance(blocks, list):
        raise ValueError("no content array")
    text = "".join(
        b["text"] for b in blocks
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
    )
    stop = _get(v, "stop_reason")
    return (
        text,
        _i64(_get(v, "usage", "input_tokens")),
        _i64(_get(v, "usage", "output_tokens")),
        stop if isinstance(stop, str) else None,
    )


def parse_openai(v: Any) -> Parsed:
    """`choices[0].message.content`, token counts and `finish_reason` (`length` maps to a cut)."""
    choice = _get(v, "choices", 0)
    stop = _get(choice, "finish_reason")
    stop = stop if isinstance(stop, str) else None
    message = _get(choice, "message")
    content = message.get("content", ...) if isinstance(message, dict) else ...
    if isinstance(content, str):
        text = content
    elif content is None and stop in ("length", "content_filter"):
        # Reasoning can exhaust the output budget, or a filter withhold the text,
        # before any final text is produced.
        text = ""
    else:
        raise ValueError("no choices[0].message.content")
    return (
        text,
        _i64(_get(v, "usage", "prompt_tokens")),
        _i64(_get(v, "usage", "completion_tokens")),
        stop,
    )
