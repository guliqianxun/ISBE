"""Real SMTP send of the rendered weekly report using .env credentials.

Loads .env, then pushes the already-rendered artifact through the production
`send_digest_notification` path (real server, real delivery). Unlike
local_email_test.py (which captures to a throwaway in-process server), this one
actually delivers to ISBE_SMTP_TO.

Run: uv run python scripts/send_real_email.py
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "tmp" / "email_test" / "nowcasting_2026-W24.md"


def main() -> None:
    load_dotenv(REPO / ".env")
    from isbe.notify import is_configured, send_digest_notification

    if not is_configured():
        raise SystemExit("SMTP not configured (need ISBE_SMTP_HOST/FROM/TO in .env)")
    if not ARTIFACT.is_file():
        raise SystemExit(f"artifact missing: {ARTIFACT} (run local_email_test.py first)")

    print(f"host={os.environ['ISBE_SMTP_HOST']}:{os.getenv('ISBE_SMTP_PORT', '587')} "
          f"to={os.environ['ISBE_SMTP_TO']}")
    ok = send_digest_notification(
        topic_label="nowcasting",
        period_label="2026-W24",
        artifact_path=ARTIFACT,
        excerpt="ISBE nowcasting weekly (verifiability-first sample)",
    )
    print(f"delivered: {ok}")
    if not ok:
        raise SystemExit("send returned False — see [notify] WARN above")


if __name__ == "__main__":
    main()
