"""`isbe serve`: bind, start the report queue, and run the console on uvicorn.

Connection limits:
- a request head must arrive within `Limits.header_seconds` of connecting
  or of the previous response, else the connection is dropped (slow headers);
- idle keep-alive connections close after `Limits.keep_alive_seconds`, and a
  connection whose client stops reading its response is dropped
  `Limits.send_seconds` later;
- at most `Limits.connections` open connections (uvicorn answers 503
  beyond that) and `Limits.connections_per_peer` per client address, except
  for trusted proxies (`ISBE_TRUSTED_PROXIES`, see guards.py).
Body limits and the handler pool are in `app.py`.
"""

import asyncio
import ipaddress
import logging
import re
import secrets
import socket
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol

from isbe import auth
from isbe.core import db, paths
from isbe.reports.library import Articles

from .app import create_app
from .console import Limits, SourcesRoute
from .generation_api import ManagerFactory, UnavailableManager
from .guards import TrustedProxies, loopback

log = logging.getLogger(__name__)

_PORT = re.compile(r"[0-9]{1,5}")


def parse_bind(bind: str) -> tuple[str, int, socket.AddressFamily]:
    """`a.b.c.d:port` or `[v6]:port` as (host, port, address family); ValueError otherwise."""
    failure = ValueError("bind must be an IP address and port")
    if bind.startswith("["):
        host, sep, port = bind[1:].partition("]:")
        family = socket.AF_INET6
    else:
        host, sep, port = bind.rpartition(":")
        family = socket.AF_INET
    if not sep or not _PORT.fullmatch(port) or int(port) > 0xFFFF:
        raise failure
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0] if family == socket.AF_INET6 else host)
    except ValueError:
        raise failure from None
    if (ip.version == 6) != (family == socket.AF_INET6):
        raise failure
    return host, int(port), family


def protocol(limits: Limits, trusted: TrustedProxies) -> type[H11Protocol]:
    """uvicorn's h11 protocol with header deadlines and per-peer connection caps."""
    peers: dict[str, int] = {}

    class ConsoleProtocol(H11Protocol):
        _deadline: asyncio.TimerHandle | None = None
        _seen: object = None
        _peer: str | None = None

        def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
            super().connection_made(transport)
            peer = self.client[0] if self.client else ""
            if peer not in trusted and peers.get(peer, 0) >= limits.connections_per_peer:
                log.warning("connection refused: too many connections from one client")
                transport.abort()
                return
            peers[peer] = peers.get(peer, 0) + 1
            self._peer = peer
            self._arm()

        def connection_lost(self, exc: Exception | None) -> None:
            self._disarm()
            if self._peer is not None:
                left = peers.get(self._peer, 1) - 1
                if left > 0:
                    peers[self._peer] = left
                else:
                    peers.pop(self._peer, None)
                self._peer = None
            super().connection_lost(exc)

        def data_received(self, data: bytes) -> None:
            super().data_received(data)
            self._note_request()

        def on_response_complete(self) -> None:
            before = self.cycle
            super().on_response_complete()
            if self.cycle is before and not self.transport.is_closing():
                self._arm()
            else:
                self._note_request()

        def timeout_keep_alive_handler(self) -> None:
            if self.transport.get_write_buffer_size():
                # A response the client does not read must not hold the socket forever.
                self.loop.call_later(limits.send_seconds, self._abort)
            super().timeout_keep_alive_handler()

        def _note_request(self) -> None:
            if self.cycle is not None and self.cycle is not self._seen:
                self._seen = self.cycle
                self._disarm()

        def _arm(self) -> None:
            self._disarm()
            self._deadline = self.loop.call_later(limits.header_seconds, self._abort)

        def _disarm(self) -> None:
            if self._deadline is not None:
                self._deadline.cancel()
                self._deadline = None

        def _abort(self) -> None:
            if not self.transport.is_closing() or self.transport.get_write_buffer_size():
                self.transport.abort()

    return ConsoleProtocol


def server_config(app: Any, limits: Limits, trusted: TrustedProxies) -> uvicorn.Config:
    return uvicorn.Config(
        app,
        http=protocol(limits, trusted),
        lifespan="off",
        # X-Forwarded-For is read only from trusted proxies, in guards.py.
        proxy_headers=False,
        server_header=False,
        access_log=False,
        timeout_keep_alive=max(1, round(limits.keep_alive_seconds)),
        limit_concurrency=limits.connections,
        log_level="warning",
        # Open event streams last minutes; on shutdown they are cut after this.
        timeout_graceful_shutdown=5,
    )


def upgrade(data: Path, say: Callable[[str], None] = lambda line: print(line, flush=True)) -> None:
    """Migrate (and compact) every database of the data directory before the
    socket is bound, so no request waits for a migration (016 takes about 30 s
    per GB). The root database must work or the console does not start; a
    member workspace that cannot be upgraded (corrupt, too new, locked, a link)
    is logged and skipped, and only its owner's requests fail, as before."""
    root = data / "isbe.db"
    if root.is_file():
        try:
            db.upgrade(root, say)
        except (sqlite3.Error, db.SchemaTooNew) as error:
            raise RuntimeError(f"{root}: {error}") from error
    every = data / "workspaces"
    try:
        for ancestor in (data, *data.parents, every):
            paths.check_path(ancestor)
        members = sorted(every.iterdir()) if every.is_dir() else []
    except (paths.UnsafePath, OSError) as error:
        log.error("workspaces in %s were not upgraded: %s", data, error)
        return
    for member in members:
        path = member / "isbe.db"
        try:
            paths.check_path(member)
            paths.check_path(path)
            if path.is_file():
                db.upgrade(path, say)
        except (sqlite3.Error, db.SchemaTooNew, paths.UnsafePath, OSError) as error:
            log.error("could not upgrade %s, its owner's requests will fail: %s", path, error)


def serve(
    data: Path,
    bind: str,
    shared: bool,
    secure_cookies: bool,
    *,
    generation: ManagerFactory | None = None,
    articles: Articles | None = None,
    sources: SourcesRoute | None = None,
    trusted_proxies: TrustedProxies | None = None,
    limits: Limits | None = None,
) -> None:
    """Serve the console until interrupted; a failure to start raises `RuntimeError`.

    `generation` opens the report queue and starts its worker (its
    `Manager.open`); it runs after the socket is bound and is closed when
    the server stops. Without it the console runs with `UnavailableManager`
    (no tasks; submissions answer 500 "queue unavailable"), which the
    integrator replaces by passing `isbe.generation`'s factory here or from
    `cli._serve`.
    """
    host, port, family = parse_bind(bind)
    if not shared and not loopback(ipaddress.ip_address(host.split("%", 1)[0])):
        raise RuntimeError("Use --shared for network access; local mode only listens on localhost.")
    upgrade(data)
    with closing(db.connect(data / "isbe.db")) as conn:
        if shared and not auth.has_admin(conn):
            raise RuntimeError("Create an administrator with account-add --admin before starting shared mode.")
    trusted = trusted_proxies if trusted_proxies is not None else TrustedProxies.from_env()
    limits = limits or Limits()
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError as e:
        sock.close()
        # The OS error text and number: "Address already in use (os error 98)".
        detail = f"{e.strerror} (os error {e.errno})" if e.errno else str(e)
        raise RuntimeError(f"bind {bind}: {detail}") from None
    bound = sock.getsockname()
    local_port = bound[1] if loopback(ipaddress.ip_address(bound[0].split("%", 1)[0])) else None
    if generation is None:
        log.warning("report queue not wired in: tasks are unavailable")
    try:
        manager = (generation or UnavailableManager)(data)
    except BaseException:
        sock.close()
        raise
    try:
        app = create_app(
            data,
            token=secrets.token_hex(32),
            local_port=local_port,
            shared=shared,
            secure_cookies=secure_cookies,
            generation=manager,
            articles=articles,
            sources=sources,
            trusted_proxies=trusted,
            limits=limits,
        )
        print(f"ISBE console on http://{bind}", flush=True)
        uvicorn.Server(server_config(app, limits, trusted)).run(sockets=[sock])
    finally:
        sock.close()
        manager.close()

