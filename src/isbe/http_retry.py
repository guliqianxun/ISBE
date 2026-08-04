"""Shared tenacity retry policy for plain-httpx clients.

Extracted from the pattern already duplicated in llm/client.py and
topics/_shared/arxiv.py — new HTTP clients should use this instead of copying
the frozenset a third time. (Folding the two existing copies onto this helper
is a separate cleanup.)
"""

import time

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRYABLE_HTTPX_EXC = (
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    # mid-response resets — routine when the metrail service restarts
    httpx.ReadError,
    httpx.WriteError,
)


def is_retryable_http_error(exc: BaseException) -> bool:
    if isinstance(exc, RETRYABLE_HTTPX_EXC):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUSES
    return False


def _sleep_between_attempts(seconds: float) -> None:
    """Test seam for tenacity's sleep callback."""
    time.sleep(seconds)


HTTP_RETRY = retry(
    retry=retry_if_exception(is_retryable_http_error),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    # late-bound so tests can patch _sleep_between_attempts after import
    sleep=lambda s: _sleep_between_attempts(s),
    reraise=True,
)
