"""A scripted local SMTP server (sockets and a thread) and a throwaway TLS
certificate for the mail tests. No outside network."""

import base64
import shutil
import socket
import ssl
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest


@dataclass(frozen=True)
class Certificate:
    cert: Path
    key: Path

    def client(self) -> ssl.SSLContext:
        """A verifying client context that trusts only this certificate."""
        context = ssl.create_default_context(cafile=str(self.cert))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context

    def server(self) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, self.key)
        return context


@pytest.fixture(scope="session")
def certificate(tmp_path_factory: pytest.TempPathFactory) -> Certificate:
    """A self-signed certificate for the name localhost (not its address), made with the openssl CLI."""
    if shutil.which("openssl") is None:
        pytest.skip("openssl is not installed")
    folder = tmp_path_factory.mktemp("tls")
    cert, key = folder / "cert.pem", folder / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
         "-nodes", "-days", "2", "-subj", "/CN=localhost", "-keyout", str(key), "-out", str(cert),
         "-addext", "subjectAltName=DNS:localhost"],
        check=True,
        capture_output=True,
    )
    return Certificate(cert, key)


@dataclass
class SmtpServer:
    """One scripted SMTP peer on 127.0.0.1 that records what the client sent.

    `mode` is "starttls" (plain greeting, STARTTLS offered unless
    `offer_starttls` is false) or "tls" (TLS from the first byte).
    `data_reply` answers the end of DATA; None closes the connection instead.
    """

    mode: str = "starttls"
    tls: ssl.SSLContext | None = None
    offer_starttls: bool = True
    auth: str = "PLAIN LOGIN"
    data_reply: str | None = "250 queued"
    greeting: str | None = "220 localhost ready"
    replies: dict[str, str] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    messages: list[bytes] = field(default_factory=list)
    credentials: list[tuple[str, str]] = field(default_factory=list)
    connections: int = 0
    errors: list[BaseException] = field(default_factory=list)
    encrypted: list[bool] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port: int = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._listener.close()
        self._thread.join(5)

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            self.connections += 1
            conn.settimeout(5)
            try:
                self._session(conn)
            except (OSError, ssl.SSLError) as error:
                self.errors.append(error)
            finally:
                conn.close()

    def _session(self, conn: socket.socket) -> None:
        if self.mode == "tls":
            assert self.tls is not None
            conn = self.tls.wrap_socket(conn, server_side=True)
        stream = conn.makefile("rb")
        encrypted = self.mode == "tls"
        if self.greeting is None:
            stream.readline()  # say nothing; wait for the client to give up
            return
        conn.sendall(f"{self.greeting}\r\n".encode())

        def send(text: str) -> None:
            conn.sendall(f"{text}\r\n".encode())

        while True:
            raw = stream.readline()
            if not raw:
                return
            line = raw.decode()
            self.commands.append(line)
            verb = line.split(" ", 1)[0].rstrip("\r\n").upper()
            if verb in self.replies:
                send(self.replies[verb])
            elif verb == "EHLO":
                lines = ["localhost"]
                if self.mode == "starttls" and self.offer_starttls and not encrypted:
                    lines.append("STARTTLS")
                if self.auth:
                    lines.append(f"AUTH {self.auth}")
                send("\r\n".join([f"250-{x}" for x in lines[:-1]] + [f"250 {lines[-1]}"]))
            elif verb == "STARTTLS":
                assert self.tls is not None
                send("220 ready")
                conn = self.tls.wrap_socket(conn, server_side=True)
                stream = conn.makefile("rb")
                encrypted = True
            elif verb == "AUTH":
                self.encrypted.append(encrypted)
                parts = line.split()
                if parts[1] == "PLAIN":
                    _, user, password = base64.b64decode(parts[2]).decode().split("\0")
                else:
                    send("334 " + base64.b64encode(b"Username:").decode())
                    user = base64.b64decode(stream.readline()).decode()
                    send("334 " + base64.b64encode(b"Password:").decode())
                    password = base64.b64decode(stream.readline()).decode()
                self.credentials.append((user, password))
                send(self.replies.get("AUTH-RESULT", "235 authenticated"))
            elif verb in ("MAIL", "RCPT"):
                send("250 ok")
            elif verb == "DATA":
                send("354 send data")
                data = b""
                while (chunk := stream.readline()) != b".\r\n":
                    assert chunk, "connection closed inside DATA"
                    data += chunk[1:] if chunk.startswith(b".") else chunk  # undo dot-stuffing
                self.messages.append(data)
                if self.data_reply is None:
                    return
                send(self.data_reply)
            elif verb == "QUIT":
                send("221 bye")
                return
            else:
                send("500 unexpected command")


@pytest.fixture
def smtp_server() -> Iterator[type[SmtpServer]]:
    """`smtp_server(**options)` starts an `SmtpServer`; all are closed after the test."""
    started: list[SmtpServer] = []

    class Started(SmtpServer):
        def __post_init__(self) -> None:
            super().__post_init__()
            started.append(self)

    yield Started
    for server in started:
        server.close()
