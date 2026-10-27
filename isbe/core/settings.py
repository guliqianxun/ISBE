"""Service settings shared by the CLI and the console.

A non-empty environment variable overrides the private `<data>/settings.json`.
A process reads that file once (`init`); `save` refreshes the cache, so the
console sees its own writes. A queued task runs inside `task_scope` with the
snapshot taken by `for_task`, which then wins for every supported key.
"""

import contextvars
import os
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from isbe.core import jsonfmt, paths, validators
from isbe.core.validators import Invalid

type Values = dict[str, str]

KEYS: tuple[str, ...] = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_SECURITY",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "EMAIL_TRANSPORT",
    "ZEPTOMAIL_API_URL",
    "ZEPTOMAIL_TOKEN",
    "SMTP_FROM",
    "SMTP_FROM_NAME",
    "ISBE_EMAIL_DEFAULT_RECIPIENTS",
    "ISBE_LLM_PROVIDER",
    "ISBE_LLM_NO_AUTH",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "ISBE_LLM_BASE_URL",
    "ISBE_LLM_MODEL_FAST",
    "ISBE_LLM_MODEL_SMART",
    "ISBE_LLM_MAX_TOKENS",
    "ISBE_LLM_TIMEOUT_S",
    "ISBE_LLM_EXTRA_BODY",
    "ISBE_PAPER_SOURCE",
    "OPENALEX_API_URL",
    "OPENALEX_API_KEY",
    "OPENALEX_MAILTO",
    "ESSAIES_URL",
    "ISBE_FULLTEXT_EXTRACTOR",
    "METRAIL_API_URL",
    "GITHUB_TOKEN",
    "ARXIV_API_URL",
    "GITHUB_API_URL",
    "SEARXNG_URL",
    "ISBE_ARTICLE_DNS",
)
# Settings of removed features (Baidu search, 2026-10-21). A settings.json saved
# before still holds them; `read` drops them instead of refusing the whole file,
# so the next `save` writes the file without them.
RETIRED = frozenset({"BAIDU_SEARCH_API_KEY", "ISBE_SEARCH_DAILY_LIMIT"})
SECRETS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "OPENALEX_API_KEY",
        "ZEPTOMAIL_TOKEN",
        "SMTP_PASSWORD",
    }
)
ARXIV_API = "https://export.arxiv.org/api/query"
OPENALEX_API = "https://api.openalex.org"
GITHUB_API = "https://api.github.com"
LLM_TIMEOUT = 900
MAX_FILE = 64 * 1024

_saved: Values = {}
_saved_for: Path | None = None
_task: contextvars.ContextVar[Values | None] = contextvars.ContextVar("isbe_task_settings", default=None)


def environment(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value and value.strip() else None


def init(data: Path) -> None:
    """Load the saved settings for this process."""
    global _saved, _saved_for
    _saved = read(data)
    _saved_for = data


def get(name: str) -> str | None:
    values = _task.get()
    if values is not None and name in KEYS:
        return values.get(name)
    return environment(name) or _saved.get(name)


@contextmanager
def task_scope(values: Values) -> Iterator[None]:
    """Use a `for_task` snapshot for the block without touching the process environment."""
    token = _task.set(dict(values))
    try:
        yield
    finally:
        _task.reset(token)


def read(data: Path) -> Values:
    """Validated contents of `<data>/settings.json`.

    Missing file → `{}`; larger than MAX_FILE or not a JSON object of strings →
    `Invalid` (whose message never repeats the file's contents); `RETIRED` names
    are dropped and every other entry passes `validate`, which may drop empty
    values. Other I/O errors propagate.
    """
    path = data / "settings.json"
    try:
        with open(path, "rb") as file:
            raw = file.read(MAX_FILE + 1)
    except FileNotFoundError:
        return {}
    if len(raw) > MAX_FILE:
        raise Invalid("settings.json is too large")
    malformed = Invalid("settings.json must map supported names to strings")
    try:
        values = jsonfmt.loads(raw)
    except ValueError:
        raise malformed from None
    if not isinstance(values, dict) or not all(isinstance(v, str) for v in values.values()):
        raise malformed
    checked: Values = {}
    for name in sorted(values.keys() - RETIRED):
        value = validate(name, values[name])
        if value is not None:
            checked[name] = value
    return checked


def for_task(data: Path) -> Values:
    """File values with validated non-empty environment overrides."""
    return _for_task(data, environment)


def _for_task(data: Path, env: Callable[[str], str | None]) -> Values:
    values = read(data)
    for name in KEYS:
        value = validate(name, env(name))
        if value is not None:
            values[name] = value
    return values


def validate(name: str, value: str | None) -> str | None:
    """Normalize and check one setting; unknown names are errors."""
    if name not in KEYS:
        raise Invalid("unsupported setting name")
    return validators.setting(name, value)


def snapshot(data: Path) -> dict[str, Any]:
    """The redacted view for `/api/settings` and `/api/session`.

    Must not raise for a bad environment value: report it in `llm.error`
    so the console still loads.
    """
    return _snapshot(read(data), environment)


def _snapshot(saved: Values, env: Callable[[str], str | None]) -> dict[str, Any]:
    from isbe.core.llm import (
        Llm,  # llm reads settings; import late to keep the cycle out
    )

    def effective(name: str) -> tuple[str, str] | None:
        value = env(name)
        if value is not None and validators.trim(value):
            if name.startswith("ISBE_LLM_") or name == "ISBE_FULLTEXT_EXTRACTOR":
                value = validators.trim(value)
            return value, "environment"
        if name in saved:
            return saved[name], "file"
        return None

    provider = (effective("ISBE_LLM_PROVIDER") or ("anthropic", ""))[0]
    try:
        base, fast, smart = validators.provider_defaults(provider)
    except Invalid:
        # An unsupported (or mis-cased) provider from the environment has no defaults;
        # the readiness check below names the problem instead of failing the page.
        base = fast = smart = ""
    defaults = {
        "EMAIL_TRANSPORT": "zeptomail_api"
        if effective("ZEPTOMAIL_API_URL") or effective("ZEPTOMAIL_TOKEN")
        else "smtp",
        "SMTP_SECURITY": "tls",
        "ISBE_LLM_PROVIDER": "anthropic",
        "ISBE_LLM_NO_AUTH": "false",
        "ISBE_LLM_BASE_URL": base,
        "ISBE_LLM_MODEL_FAST": fast,
        "ISBE_LLM_MODEL_SMART": smart,
        "ISBE_LLM_TIMEOUT_S": str(LLM_TIMEOUT),
        "ISBE_LLM_EXTRA_BODY": "{}",
        "ISBE_FULLTEXT_EXTRACTOR": "metrail" if effective("METRAIL_API_URL") else "llm",
        "ISBE_PAPER_SOURCE": paper_source(lambda name: (effective(name) or (None, ""))[0]),
        "OPENALEX_API_URL": OPENALEX_API,
        "ARXIV_API_URL": ARXIV_API,
        "GITHUB_API_URL": GITHUB_API,
        "ISBE_ARTICLE_DNS": "system",
    }
    values: dict[str, str] = {}
    configured: dict[str, bool] = {}
    sources: dict[str, str] = {}
    for name in KEYS:
        value, source = effective(name) or (defaults.get(name, ""), "default")
        if name in SECRETS:
            configured[name] = bool(value)
        else:
            values[name] = value
        sources[name] = source
    provider_defaults = {}
    for name in validators.PROVIDERS:
        base, fast, smart = validators.provider_defaults(name)
        provider_defaults[name] = {
            "ISBE_LLM_BASE_URL": base,
            "ISBE_LLM_MODEL_FAST": fast,
            "ISBE_LLM_MODEL_SMART": smart,
        }
    # The same constructor checks CLI readiness; it sends no request.
    try:
        Llm.from_vars(lambda name: (effective(name) or (None, ""))[0])
        error = None
    except Exception as failure:  # any construction failure is a readiness message
        error = str(failure)
    return {
        "values": values,
        "configured": configured,
        "sources": sources,
        "provider_defaults": provider_defaults,
        "llm": {"ready": error is None, "error": error, "connection_verified": False},
    }


def paper_source(value: Callable[[str], str | None] = get) -> str:
    """Where question runs search for papers: `ISBE_PAPER_SOURCE`, else essaies when
    `ESSAIES_URL` is set (an existing deployment keeps its source), else the OpenAlex
    API, which needs nothing set up."""
    return value("ISBE_PAPER_SOURCE") or ("essaies" if value("ESSAIES_URL") else "openalex")


# Keeps concurrent read/modify/replace operations in this process from losing fields.
_WRITES = threading.Lock()


def save(data: Path, changes: Mapping[str, str | None]) -> None:
    """Validate and atomically write changes, `None` clearing a key.

    Missing names stay unchanged; null or blank removes an override. Mode 0600,
    unique temporary name (a crashed save must not block later
    ones), fsync, rename, an in-process lock; refreshes the process cache when
    `data` is the directory `init` loaded. An invalid change leaves the file as
    it was.
    """
    global _saved
    with _WRITES:
        values = read(data)
        for name in sorted(changes):
            value = validate(name, changes[name])
            if value is None:
                values.pop(name, None)
            else:
                values[name] = value
        body = (jsonfmt.dumps_pretty(values) + "\n").encode("utf-8")
        if len(body) > MAX_FILE:
            raise Invalid(f"settings.json would exceed {MAX_FILE} bytes")
        data.mkdir(parents=True, exist_ok=True)
        paths.atomic_write(data / "settings.json", body, mode=0o600)
        if _saved_for is None or _same(_saved_for, data):
            _saved = values


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False
