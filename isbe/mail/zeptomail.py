"""ZeptoMail API delivery.

One POST through `core.http.Http` with the whole-request timeout, no
redirects and no retries. The answer is reduced to a state, a fixed message
and the provider's request ID when it looks like one (never the token);
nothing else from the response is kept or logged.
"""

import logging
from typing import Any

from isbe.core import jsonfmt
from isbe.core.http import Http, read_capped

from .config import Config
from .outcome import UNKNOWN_PROVIDER, outcome

log = logging.getLogger(__name__)

MAX_RESPONSE = 64 * 1024
# ZeptoMail's success codes: EM_104 (current API), TM_6601 (legacy).
_ACCEPTED_CODES = frozenset({"EM_104", "TM_6601"})
_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
ACCEPTED = "The provider accepted this request. Inbox delivery is not yet confirmed."
_FAILED = {
    401: "The provider rejected authentication or sender authorization. Check server mail settings.",
    403: "The provider rejected authentication or sender authorization. Check server mail settings.",
    413: "The provider rejected the email size; no successful submission was confirmed.",
    429: "The provider rate limit rejected the request. No automatic retry was made.",
}
_REJECTED = "The provider rejected the request. Check its log using the request ID."


def _post(config: Config, body: dict[str, Any], timeout: float) -> tuple[int, Any] | None:
    """The status and parsed JSON answer (None when unreadable), or None without a response."""
    client = Http(timeout, max_redirects=0)
    try:
        with client.stream(
            "POST",
            config.endpoint,
            headers={"Authorization": config.authorization, "Accept": "application/json"},
            json=body,
            follow_redirects=False,
        ) as response:
            status = response.status_code
            try:
                value = jsonfmt.loads(read_capped(response, MAX_RESPONSE).decode("utf-8"))
            except Exception:  # too large, not UTF-8, not JSON or cut off: judged by status alone
                value = None
            return status, value
    except Exception as error:  # no response at all: the request may or may not have arrived
        log.warning("mail transport=zeptomail_api: no response (%s)", type(error).__name__)
        return None
    finally:
        client.close()


def _request_id(value: Any, authorization: str) -> str | None:
    if not isinstance(value, dict):
        return None
    if "request_id" in value:
        candidate = value["request_id"]
    elif isinstance(value.get("error"), dict):
        candidate = value["error"].get("request_id")
    else:
        candidate = None
    secret = authorization.split(" ", 1)[1] if " " in authorization else ""
    if (
        isinstance(candidate, str)
        and candidate
        and len(candidate) <= 200
        and set(candidate) <= _ID_CHARS
        and secret not in candidate
    ):
        return candidate
    return None


def _accepted(value: Any) -> bool:
    if not isinstance(value, dict) or "error" in value:
        return False
    data = value.get("data")
    return (
        isinstance(data, list)
        and bool(data)
        and all(isinstance(e, dict) and e.get("code") in _ACCEPTED_CODES for e in data)
    )


def deliver(config: Config, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    answer = _post(config, body, timeout)
    if answer is None:
        return outcome("unknown", UNKNOWN_PROVIDER)
    status, value = answer
    request_id = _request_id(value, config.authorization)
    if 200 <= status < 300 and _accepted(value):
        result = outcome("accepted", ACCEPTED, request_id)
    elif 400 <= status < 500:
        result = outcome("failed", _FAILED.get(status, _REJECTED), request_id)
    else:
        result = outcome("unknown", UNKNOWN_PROVIDER, request_id)
    log.info("mail state=%s request_id=%s", result["state"], request_id or "unavailable")
    return result
