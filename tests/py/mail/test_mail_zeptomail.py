"""ZeptoMail API delivery against the local `serve` fixture."""

import dataclasses
import json
import threading
import time
from http.server import BaseHTTPRequestHandler
from typing import Any

import pytest

from isbe import mail
from isbe.mail import zeptomail
from isbe.mail.compose import payload
from isbe.mail.outcome import UNKNOWN_PROVIDER

CASES = [
    (200, '{"data":[{"code":"EM_104"}],"request_id":"req-ok"}', 0, "accepted", "req-ok"),
    (201, '{"data":[{"code":"TM_6601"}],"request_id":"legacy-ok"}', 0, "accepted", "legacy-ok"),
    (202, '{"data":[{"code":"TM_6601"}],"request_id":"queued-ok"}', 0, "accepted", "queued-ok"),
    (401, '{"error":{"message":"dummy-secret","request_id":"req-auth"}}', 0, "failed", "req-auth"),
    (429, '{"error":{"message":"dummy-secret"}}', 0, "failed", None),
    (500, '{"error":{"message":"dummy-secret"}}', 0, "unknown", None),
    (200, '{"data":[{"code":"NOT_SUCCESS"}],"request_id":"dummy-secret"}', 0, "unknown", None),
    (200, "not-json dummy-secret", 0, "unknown", None),
    (302, "", 0, "unknown", None),
    (200, '{"data":[{"code":"EM_104"}]}', 0.25, "unknown", None),
]


@pytest.fixture(autouse=True)
def no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(name, raising=False)


def config() -> mail.Config:
    return mail.load(
        {
            "ZEPTOMAIL_API_URL": "https://api.zeptomail.com/v1.1/email",
            "ZEPTOMAIL_TOKEN": "dummy-secret",
            "SMTP_FROM": "sender@example.com",
            "SMTP_FROM_NAME": "研究报告",
        }.get
    )


@pytest.mark.parametrize(("status", "response", "delay", "expected", "request_id"), CASES)
def test_mock_provider_checks_wire_contract_errors_timeout_and_no_retries(
    serve: Any, status: int, response: str, delay: float, expected: str, request_id: str | None
) -> None:
    seen: list[dict[str, Any]] = []
    lock = threading.Lock()

    def handle(req: BaseHTTPRequestHandler) -> None:
        body = req.rfile.read(int(req.headers["Content-Length"]))
        with lock:
            seen.append({"path": req.path, "method": req.command, "headers": dict(req.headers),
                         "body": json.loads(body)})
        time.sleep(delay)
        serve.reply(req, status, response, {"Location": "/do-not-follow", "Content-Type": "application/json"})

    base = serve(handle)
    target = dataclasses.replace(config(), endpoint=f"{base}/v1.1/email")
    body = payload(target, ["recipient@example.com"], "测试", "中文", "<p>中文</p>", [])
    # Only the delayed case exercises a short timeout; normal replies tolerate CI load.
    result = mail.deliver(target, body, 5 if delay == 0 else 0.15)
    time.sleep(0.1 + delay)
    assert result["state"] == expected, f"HTTP {status}: {response}"
    assert result["request_id"] == request_id
    assert "dummy-secret" not in json.dumps(result)
    assert len(seen) == 1, "must not retry or follow redirects"
    request = seen[0]
    assert request["path"] == "/v1.1/email" and request["method"] == "POST"
    assert request["headers"]["Authorization"] == "Zoho-enczapikey dummy-secret"
    assert request["headers"]["Accept"] == "application/json"
    assert request["headers"]["Content-Type"].startswith("application/json")
    sent = request["body"]
    assert sent["from"]["address"] == "sender@example.com"
    assert sent["to"][0]["email_address"]["address"] == "recipient@example.com"
    assert sent["htmlbody"] == "<p>中文</p>" and sent["textbody"] == "中文"
    if expected == "unknown":
        assert result["message"] == UNKNOWN_PROVIDER


def test_status_specific_failure_messages_and_request_id_rules(serve: Any) -> None:
    answers: list[tuple[int, str]] = []

    def handle(req: BaseHTTPRequestHandler) -> None:
        req.rfile.read(int(req.headers["Content-Length"]))
        status, text = answers.pop(0)
        serve.reply(req, status, text)

    base = serve(handle)
    target = dataclasses.replace(config(), endpoint=f"{base}/email")
    body = payload(target, ["r@example.com"], "s", "t", "<p>t</p>", [])
    for status, text, message, request_id in [
        (403, "{}", zeptomail._FAILED[401], None),
        (413, '{"request_id": "id with space"}', zeptomail._FAILED[413], None),
        (400, '{"request_id": 7, "error": {"request_id": "nested"}}', zeptomail._REJECTED, None),
        (422, '{"error": {"request_id": "nested.id_1-2"}}', zeptomail._REJECTED, "nested.id_1-2"),
        (200, '{"data": [], "request_id": "empty"}', UNKNOWN_PROVIDER, "empty"),
        (200, '{"data": [{"code": "EM_104"}], "error": null}', UNKNOWN_PROVIDER, None),
        (200, '{"data": [{"code": "EM_104"}], "request_id": "' + "x" * 201 + '"}', None, None),
        (200, '{"data": [{"code": "EM_104"}], "request_id": "a-dummy-secret-b"}', None, None),
    ]:
        answers.append((status, text))
        result = mail.deliver(target, body, 5)
        if message is not None:
            assert result["message"] == message, text
        assert result["request_id"] == request_id, text


def test_large_or_unreachable_answers_are_unknown(serve: Any) -> None:
    def handle(req: BaseHTTPRequestHandler) -> None:
        req.rfile.read(int(req.headers["Content-Length"]))
        serve.reply(req, 200, '{"data":[{"code":"EM_104"}],"pad":"' + "x" * zeptomail.MAX_RESPONSE + '"}')

    base = serve(handle)
    target = dataclasses.replace(config(), endpoint=f"{base}/email")
    body = payload(target, ["r@example.com"], "s", "t", "<p>t</p>", [])
    assert mail.deliver(target, body, 5)["state"] == "unknown"
    unreachable = dataclasses.replace(config(), endpoint="http://127.0.0.1:9/email")
    assert mail.deliver(unreachable, body, 2) == {"state": "unknown", "message": UNKNOWN_PROVIDER,
                                                  "request_id": None}
