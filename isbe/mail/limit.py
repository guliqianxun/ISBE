"""Per-account send limits for manual email.

Ordinary members may send reports through the administrator's mail
configuration to recipients they choose; that is intended, but without a
limit one account could use it to send unlimited mail. Every delivery that
reaches a transport counts once, whatever its outcome (an `unknown` result
may still have been delivered); requests rejected before that (bad
recipients, missing report, mail not configured) do not.

Limits are sliding windows: members 10 emails per hour and 30 per day,
administrators 60 per hour and 300 per day. Administrators are not exempt —
the higher limit only stops a runaway client or a stolen session from
draining the provider quota and the sender's reputation.

The counters live in memory, per process. SQLite would survive restarts,
but no table fits without a migration: the generic counter table,
`auth_login_attempts`, is pruned to 15 minutes by every login attempt and its
row count feeds the
login flood cap, so mail rows would be cut short and could lock logins.
A restart is an administrator action, so a member cannot reset their own
counter; with several server processes each keeps its own.
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .config import MailError


@dataclass(frozen=True)
class Window:
    """At most `limit` sends per `seconds`; `label` names the period in messages."""

    limit: int
    seconds: int
    label: str


MEMBER: tuple[Window, ...] = (Window(10, 3600, "hour"), Window(30, 86400, "day"))
ADMIN: tuple[Window, ...] = (Window(60, 3600, "hour"), Window(300, 86400, "day"))


def _wait(seconds: float) -> str:
    minutes = max(1, math.ceil(seconds / 60))
    if minutes < 120:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{math.ceil(minutes / 60)} hours"


class Limiter:
    """Thread-safe sliding-window counters keyed by account id.

    The web layer keeps one instance for the process and calls `acquire`
    through `mail.report` / `mail.test`, right before the transport is used.
    """

    def __init__(
        self,
        member: Sequence[Window] = MEMBER,
        admin: Sequence[Window] = ADMIN,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._windows = {False: tuple(member), True: tuple(admin)}
        self._clock = clock
        self._sent: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def acquire(self, account: str, *, admin: bool) -> None:
        """Count one send for `account`, or raise `MailError` (429) without counting it."""
        if not account:
            raise ValueError("a rate-limited send needs the account id")
        windows = self._windows[admin]
        longest = max((w.seconds for w in (*self._windows[False], *self._windows[True])), default=0)
        with self._lock:
            now = self._clock()
            sent = self._sent.setdefault(account, deque())
            while sent and sent[0] <= now - longest:
                sent.popleft()
            for window in windows:
                recent = [t for t in sent if t > now - window.seconds]
                if len(recent) >= window.limit:
                    retry = recent[len(recent) - window.limit] + window.seconds - now
                    raise MailError(
                        f"Email limit reached: this account can send {window.limit} emails per "
                        f"{window.label}. Try again in {_wait(retry)}.",
                        status=429,
                        retry_after=max(1, math.ceil(retry)),
                    )
            sent.append(now)
