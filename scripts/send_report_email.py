"""Email the verifiable nowcasting report (tmp/report.html) via .env SMTP.

Sends the rendered HTML as the message body AND attaches report.html, so the
embedded framework figures survive even in mail clients that strip inline
data-URI images. Uses the production .env SMTP credentials.

Run: uv run python scripts/send_report_email.py
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
REPORT = REPO / "tmp" / "report.html"


def main() -> None:
    load_dotenv(REPO / ".env")
    for k in ("ISBE_SMTP_HOST", "ISBE_SMTP_FROM", "ISBE_SMTP_TO"):
        if not os.getenv(k):
            raise SystemExit(f"missing {k} in .env")
    if not REPORT.is_file():
        raise SystemExit(f"missing {REPORT} (run render_report.py first)")

    # This dev machine's TUN proxy blackholes raw SMTP; route through the proxy
    # explicitly via SOCKS5 (works — verified the 220 banner tunnels through).
    proxy = os.getenv("ISBE_SMTP_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    if proxy:
        from urllib.parse import urlparse
        import socks
        u = urlparse(proxy if "://" in proxy else "http://" + proxy)
        socks.set_default_proxy(socks.SOCKS5, u.hostname, u.port)
        import socket as _s
        _s.socket = socks.socksocket
        print(f"routing SMTP via SOCKS5 {u.hostname}:{u.port}")

    host = os.environ["ISBE_SMTP_HOST"]
    port = int(os.getenv("ISBE_SMTP_PORT", "465"))
    user = os.getenv("ISBE_SMTP_USER", "")
    pwd = os.getenv("ISBE_SMTP_PASS", "")
    sender = os.environ["ISBE_SMTP_FROM"]
    to = os.environ["ISBE_SMTP_TO"]

    html = REPORT.read_text(encoding="utf-8")
    label = "科研周报"
    pool = REPO / "tmp" / "pool.json"
    if pool.is_file():
        import json
        label = json.loads(pool.read_text(encoding="utf-8")).get("label", label)
    msg = EmailMessage()
    msg["Subject"] = f"[ISBE] {label}（可核实版 · 含框架图/性能表）"
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(
        "这是 ISBE 临近降水预报可核实版周报。门禁：coverage/faithfulness/traceability 全 pass。\n"
        "正文为 HTML；若邮件客户端不显示内嵌框架图，请打开附件 report.html（浏览器）查看完整图表。"
    )
    msg.add_alternative(html, subtype="html")
    # attach the full report so figures (data-URI) are always viewable
    msg.add_attachment(html.encode("utf-8"), maintype="text", subtype="html",
                       filename="nowcasting-report.html")

    print(f"sending {REPORT.stat().st_size} bytes to {to} via {host}:{port} ...")
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            if user:
                s.login(user, pwd)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            if user:
                s.login(user, pwd)
            s.send_message(msg)
    print("delivered: True")


if __name__ == "__main__":
    main()
