"""Mail configuration, report composition and delivery (SMTP, ZeptoMail API).

Manual delivery only: one bounded attempt, no automatic retries, and no
provider text or credentials in results, errors or logs.

The console calls:

- `status(root)` for the readiness view (`/api/settings`, `/api/session`,
  `/api/mail`) and `default_recipients(root)` for `/api/mail`;
- `report(conn, data, root, topic, period, recipients, limiter=..., account=...,
  admin=...)` and `test(root, recipients, limiter=..., ...)` for the POST
  routes: they return the outcome dict (answer 200) or raise `MailError`
  (answer `error.status` with `{"state": "failed", "error": error.message}`,
  plus `Retry-After: error.retry_after` when set);
- one `Limiter()` per process, shared by both routes.
"""

from .compose import TEST_SUBJECT, subject
from .config import (
    Config,
    MailError,
    SmtpConfig,
    default_recipients,
    from_data,
    load,
    status,
)
from .limit import ADMIN, MEMBER, Limiter, Window
from .send import deliver, report, test

__all__ = [
    "ADMIN",
    "MEMBER",
    "TEST_SUBJECT",
    "Config",
    "Limiter",
    "MailError",
    "SmtpConfig",
    "Window",
    "default_recipients",
    "deliver",
    "from_data",
    "load",
    "report",
    "status",
    "subject",
    "test",
]
