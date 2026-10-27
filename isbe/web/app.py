"""The ASGI application: one FastAPI app whose only route hands every request
to the console's dispatcher (`shared.route`), plus the security headers.

FastAPI's router cannot express the console's dispatch (exact raw paths, per-route
405 texts, the SPA fallback, 404/413 bodies), so it is not used for routing;
the app object still gives uvicorn, tests and middleware the usual shape.

Bounded work per request:
- The JSON body of a write is read here, on the event loop, before any
  handler runs: at most `Limits.body_bytes` (checked against
  `Content-Length` first, then while streaming) within
  `Limits.body_seconds`, else 408 and the connection is closed. Only
  writes that pass the Host, CSRF and content-type guards are read, so an
  unauthenticated client cannot make the server buffer anything else.
- Handlers are synchronous (SQLite, files, SMTP) and run in a bounded
  thread pool (`Limits.handlers`), never on the event loop; an exception is
  logged and answered with a generic 500, and the thread goes back to the pool.
- A reply sent before the body was fully read closes the connection, so the
  rest of a refused body is never waited for.
Header and keep-alive timeouts and connection caps live in `server.py`.
"""

import logging
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
from fastapi import FastAPI
from starlette.routing import Route
from starlette.types import Message, Receive, Scope, Send

from isbe.core.errors import describe
from isbe.reports.library import Articles

from . import shared
from .console import Console, Limits, SourcesRoute
from .generation_api import Manager, UnavailableManager
from .guards import Access, TrustedProxies, client_address
from .wire import INTERNAL, BodyState, Reply, Request, error, json_reply

log = logging.getLogger(__name__)

# What the UI needs and nothing more: its own script and stylesheet, inline
# style attributes (app.js renders them), its data: favicon and report figures,
# and requests to its own API. No framing anywhere.
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", CSP.encode()),
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-resource-policy", b"same-origin"),
)


class SecurityHeaders:
    """Adds the security headers, and `Cache-Control: no-store` where a reply
    sets no cache policy, to every response that does not set its own."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secured(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers += [(k, v) for k, v in SECURITY_HEADERS if k not in present]
                if b"cache-control" not in present:
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, secured)


def _request(scope: Scope, trusted: TrustedProxies) -> Request:
    headers = [(k.decode("latin-1").lower(), v.decode("latin-1")) for k, v in scope["headers"]]
    raw = scope.get("raw_path") or scope["path"].encode("utf-8")
    client = scope.get("client")
    peer = client[0] if client else ""
    return Request(
        method=scope["method"],
        path=raw.decode("ascii", "replace"),
        # Read as lossy UTF-8: a malformed byte never fails a request.
        query=scope.get("query_string", b"").decode("utf-8", "replace"),
        headers=headers,
        client=client_address(peer, [v for k, v in headers if k == "x-forwarded-for"], trusted),
    )


def _declared(req: Request) -> tuple[bool, int | None]:
    """Whether the request carries a body, and its `Content-Length` if given."""
    length = req.header("content-length")
    if length is not None and length.strip().isdigit():
        size = int(length)
        return size > 0, size
    return req.header("transfer-encoding") is not None, None


class _TimedOut(Exception):
    pass


async def _read_body(receive: Receive, declared: int | None, limits: Limits) -> tuple[bytes | BodyState, bool]:
    """The body (or why there is none) and whether it was read to the end."""
    if declared is not None and declared > limits.body_bytes:
        return BodyState.TOO_LARGE, False
    chunks: list[bytes] = []
    size = 0
    try:
        with anyio.fail_after(limits.body_seconds):
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return BodyState.BROKEN, False
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > limits.body_bytes:
                    return BodyState.TOO_LARGE, False
                chunks.append(chunk)
                if not message.get("more_body", False):
                    return b"".join(chunks), True
    except TimeoutError:
        raise _TimedOut from None


class Endpoint:
    """The ASGI endpoint behind the catch-all route."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self.limiter = anyio.CapacityLimiter(console.limits.handlers)

    def _wants_body(self, req: Request) -> bool:
        return (
            req.method in ("POST", "PUT", "DELETE")
            and req.path.startswith("/api/")
            and self.console.access.rejects(req) is None
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        req = _request(scope, self.console.trusted)
        has_body, declared = _declared(req)
        complete = not has_body
        if self._wants_body(req):
            if has_body:
                try:
                    req.body, complete = await _read_body(receive, declared, self.console.limits)
                except _TimedOut:
                    late = error(408, "The request body was not received in time.")
                    await self._send(send, late, close=True)
                    return
            else:
                req.body = b""
        reply = await anyio.to_thread.run_sync(self._handle, req, limiter=self.limiter)
        await self._send(send, reply, close=not complete, receive=receive if complete else None)

    def _handle(self, req: Request) -> Reply:
        try:
            return shared.route(self.console, req)
        except Exception as e:
            log.error("%s %s: %s", req.method, req.url, describe(e), exc_info=e)
            return json_reply(500, {"error": INTERNAL})

    async def _send(self, send: Send, reply: Reply, *, close: bool, receive: Receive | None = None) -> None:
        headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in reply.headers]
        if reply.stream is not None and receive is not None:
            await self._stream(send, receive, reply, headers)
            return
        headers.append((b"content-length", str(len(reply.body)).encode()))
        if close:
            headers.append((b"connection", b"close"))
        with anyio.fail_after(self.console.limits.send_seconds):
            await send({"type": "http.response.start", "status": reply.status, "headers": headers})
            await send({"type": "http.response.body", "body": reply.body})


    async def _stream(self, send: Send, receive: Receive, reply: Reply, headers: list[tuple[bytes, bytes]]) -> None:
        """Send `reply.stream` until it ends, its time is up or the client goes away.
        Polls run in the default thread pool, not the handlers' one, so open streams
        never hold a handler thread while they wait."""
        stream = reply.stream
        assert stream is not None
        limit = self.console.limits.send_seconds

        async def body(chunk: bytes) -> None:
            with anyio.fail_after(limit):
                await send({"type": "http.response.body", "body": chunk, "more_body": True})

        async def gone(scope: anyio.CancelScope) -> None:
            while (await receive())["type"] != "http.disconnect":
                pass
            scope.cancel()

        with anyio.fail_after(limit):
            await send({"type": "http.response.start", "status": reply.status, "headers": headers})
        try:
            async with anyio.create_task_group() as group:
                group.start_soon(gone, group.cancel_scope)
                with anyio.move_on_after(stream.seconds):
                    if reply.body:
                        await body(reply.body)
                    quiet = 0.0
                    while True:
                        chunk = await anyio.to_thread.run_sync(stream.poll)
                        if chunk is None:
                            break
                        if chunk:
                            await body(chunk)
                            quiet = 0.0
                        else:
                            quiet += stream.interval
                            if quiet >= stream.heartbeat:
                                await body(b": keep-alive\n\n")
                                quiet = 0.0
                            await anyio.sleep(stream.interval)
                group.cancel_scope.cancel()
            with anyio.fail_after(limit):
                await send({"type": "http.response.body", "body": b""})
        except (OSError, TimeoutError) as error:
            log.info("event stream ended: %s", describe(error))
        except Exception as error:
            log.error("event stream failed: %s", describe(error), exc_info=error)
        finally:
            if stream.close is not None:
                with anyio.CancelScope(shield=True):
                    await anyio.to_thread.run_sync(stream.close)


def create_app(
    root: Path,
    *,
    token: str,
    local_port: int | None,
    shared: bool = False,
    secure_cookies: bool = False,
    generation: Manager | None = None,
    articles: Articles | None = None,
    sources: SourcesRoute | None = None,
    trusted_proxies: TrustedProxies | None = None,
    limits: Limits | None = None,
) -> FastAPI:
    """The console for data directory `root`.

    `token` is the process-wide CSRF token; `local_port` the port the server
    listens on when bound to a loopback address (None otherwise: any IP
    literal is then an allowed Host). `generation` is the report queue
    (default: `UnavailableManager`), `articles` the media briefing's source
    list (`isbe.reports.media.articles`), `sources` the `/api/sources*`
    routes (see `routes_sources.py`).
    """
    console = Console(
        root=Path(root),
        shared=shared,
        secure_cookies=secure_cookies,
        access=Access(token, local_port),
        generation=generation if generation is not None else UnavailableManager(Path(root)),
        articles=articles,
        sources=sources,
        trusted=trusted_proxies if trusted_proxies is not None else TrustedProxies(),
        limits=limits if limits is not None else Limits(),
    )
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    app.router.routes.append(Route("/{path:path}", endpoint=Endpoint(console)))
    app.add_middleware(SecurityHeaders)
    app.state.console = console
    return app
