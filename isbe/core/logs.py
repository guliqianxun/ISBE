"""Logging for the service and the CLI, configured once at start-up
(docs/standards/backend.md B-G1): records at `ISBE_LOG_LEVEL` (default INFO) and
above go to stderr as `<time> <LEVEL> <logger>: <message>`; httpx's own request
lines only from WARNING. Modules log with
`logging.getLogger(__name__)`; nothing logs a key, a token or a provider body.
"""

import logging
import os
import sys

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class _Stderr(logging.StreamHandler):  # type: ignore[type-arg]
    """stderr as it is when a record is written (tests swap it)."""

    @property  # type: ignore[override]
    def stream(self) -> object:
        return sys.stderr

    @stream.setter
    def stream(self, value: object) -> None:
        pass


def setup(environ: dict[str, str] | None = None) -> int:
    """Install the stderr handler on the root logger, once; the level it set."""
    env = os.environ if environ is None else environ
    name = (env.get("ISBE_LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, name) if name in LEVELS else logging.INFO
    root = logging.getLogger()
    if not any(isinstance(h, _Stderr) for h in root.handlers):
        handler = _Stderr()
        handler.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(handler)
    root.setLevel(level)
    # httpx logs every request with its URL at INFO: too much, and URLs carry query text.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))
    if name not in LEVELS:
        logging.getLogger(__name__).warning("ISBE_LOG_LEVEL=%r is not one of %s; using INFO", name[:20], LEVELS)
    return level
