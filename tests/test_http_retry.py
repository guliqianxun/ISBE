"""Unit tests for the shared HTTP retry policy (isbe/http_retry.py)."""

from unittest.mock import patch

import httpx
import pytest

from isbe import http_retry
from isbe.http_retry import HTTP_RETRY, is_retryable_http_error


def _status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "http://x/")
    resp = httpx.Response(code, request=req)
    return httpx.HTTPStatusError(f"{code}", request=req, response=resp)


def test_retryable_classification():
    assert is_retryable_http_error(_status_error(503))
    assert is_retryable_http_error(_status_error(429))
    assert is_retryable_http_error(httpx.ConnectError("boom"))
    assert not is_retryable_http_error(_status_error(404))
    assert not is_retryable_http_error(_status_error(422))
    assert not is_retryable_http_error(ValueError("nope"))


def test_retries_on_503_then_succeeds():
    calls = {"n": 0}

    @HTTP_RETRY
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _status_error(503)
        return "ok"

    with patch.object(http_retry, "_sleep_between_attempts", lambda s: None):
        assert flaky() == "ok"
    assert calls["n"] == 3


def test_no_retry_on_422():
    calls = {"n": 0}

    @HTTP_RETRY
    def rejected():
        calls["n"] += 1
        raise _status_error(422)

    with patch.object(http_retry, "_sleep_between_attempts", lambda s: None):
        with pytest.raises(httpx.HTTPStatusError):
            rejected()
    assert calls["n"] == 1


def test_exhausts_after_three_attempts():
    calls = {"n": 0}

    @HTTP_RETRY
    def always_down():
        calls["n"] += 1
        raise httpx.ConnectError("down")

    with patch.object(http_retry, "_sleep_between_attempts", lambda s: None):
        with pytest.raises(httpx.ConnectError):
            always_down()
    assert calls["n"] == 3
