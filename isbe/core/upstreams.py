"""What the service last heard from each upstream (essaies, OpenAlex, the model), for
`/api/health` and the console: whether the latest request answered, when, and
why not. The clients record every request they finish (`record`); nothing is
stored, so a restarted service knows nothing until the next request or probe.

`unreachable` tells a failure that says the upstream is gone (connection
refused, no route, DNS, a connect timeout) from one about a single request, so a
stage can stop at the first one (docs/standards/backend.md B-U1).
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from isbe.core import db

ESSAIES = "essaies"
OPENALEX = "openalex"
MODEL = "model"
NAMES = (ESSAIES, OPENALEX, MODEL)


@dataclass(frozen=True)
class Outcome:
    ok: bool
    at: str  # db.now() of the answer or failure
    monotonic: float
    error: str | None = None  # why it failed: safe to show, no secrets


_lock = threading.Lock()
_last: dict[str, Outcome] = {}


def record(name: str, ok: bool, error: str | None = None, *, clock: Callable[[], float] = time.monotonic) -> None:
    """Note how the latest request to upstream `name` went."""
    with _lock:
        _last[name] = Outcome(ok, db.now(), clock(), None if ok else (error or "failed"))


def last(name: str) -> Outcome | None:
    with _lock:
        return _last.get(name)


def age(name: str, *, clock: Callable[[], float] = time.monotonic) -> float | None:
    """Seconds since upstream `name` was last heard from, or None."""
    outcome = last(name)
    return None if outcome is None else clock() - outcome.monotonic


def forget() -> None:
    """Drop every outcome (tests)."""
    with _lock:
        _last.clear()


def view(name: str, configured: bool) -> dict[str, Any]:
    """`/api/health`'s entry for upstream `name`: `state` ok, down or unknown."""
    outcome = last(name)
    if not configured:
        return {"configured": False, "state": "unknown", "checked_at": None, "error": None}
    if outcome is None:
        return {"configured": True, "state": "unknown", "checked_at": None, "error": None}
    return {
        "configured": True,
        "state": "ok" if outcome.ok else "down",
        "checked_at": outcome.at,
        "error": outcome.error,
    }


def root(error: BaseException) -> BaseException:
    """The innermost cause of `error`: the network or provider failure itself."""
    seen: set[int] = set()
    while id(error) not in seen and (error.__cause__ or error.__context__) is not None:
        seen.add(id(error))
        error = error.__cause__ or error.__context__  # type: ignore[assignment]
    return error


def rejected(error: BaseException | None) -> bool:
    """Whether the model answered 401 or 403: a wrong key or a model the key may not
    use. Waiting does not help, so a stage stops at the first one."""
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if type(error).__name__ == "ProviderError" and str(error).startswith(("HTTP 401", "HTTP 403")):
            return True
        error = error.__cause__ or error.__context__
    return False


def unreachable(error: BaseException | None) -> bool:
    """Whether `error`, or any of its causes, says the upstream could not be reached
    at all: no connection was made."""
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, httpx.ConnectError | httpx.ConnectTimeout):
            return True
        error = error.__cause__ or error.__context__
    return False
