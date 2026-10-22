"""What a delivery attempt reports: `{"state", "message", "request_id"}`.

`state` is `accepted` (the server or provider took the email; inbox delivery
is not confirmed), `failed` (it was refused, or never left) or `unknown` (no
usable answer: the email may or may not have been submitted). Messages are
fixed texts; provider responses never appear in them.
"""

from typing import Any

UNKNOWN_PROVIDER = (
    "Result unknown. Check the provider log and inbox before sending again; "
    "no automatic retry was made."
)
UNKNOWN_SMTP = (
    "Result unknown. Check the SMTP server log and inbox before sending again; "
    "no automatic retry was made."
)


def outcome(state: str, message: str, request_id: str | None = None) -> dict[str, Any]:
    return {"state": state, "message": message, "request_id": request_id}
