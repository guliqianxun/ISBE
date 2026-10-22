"""Outbound HTTP. Every request of the service goes through `Http`.

Requests have one whole-request timeout covering connect, TLS,
redirects and the body; `isbe/<version>` as user agent; redirects followed up
to a configurable limit; one proxy taken from the first valid of `ALL_PROXY`,
`HTTPS_PROXY`, `HTTP_PROXY` (either case) for every scheme; and `NO_PROXY`
honoured for host names, addresses and — unlike most clients, which skip them —
CIDR entries such as `192.168.0.0/16`, so LAN services (essaies, metrail) never
go through the proxy. Bodies are read with a hard size cap.

The deadline is enforced by the network backend: every connect, TLS handshake,
read and write is limited to the time left, so a server that trickles bytes
cannot stretch a request past it. The same backend can pin connections to
vetted addresses (`resolver`), which `netguard` uses for public-only fetches.
"""

import os
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cache
from typing import Any

import httpcore
import httpx

from isbe import __version__
from isbe.core import jsonfmt, validators

USER_AGENT = f"isbe/{__version__}"
# Default cap on a response body.
MAX_BODY = 10 * 1024 * 1024
_PROXY_VARS = ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")

# Monotonic instant by which the current request must finish (None: unbounded).
_DEADLINE: ContextVar[float | None] = ContextVar("isbe_http_deadline", default=None)

# (host, port, seconds left) -> addresses to connect to, already validated.
type Resolver = Callable[[str, int, float], Sequence[str]]


class BodyTooLarge(Exception):
    pass


@contextmanager
def deadline(seconds: float) -> Iterator[None]:
    """Bound all requests inside the block to `seconds` from now; nested blocks keep the earliest."""
    end = time.monotonic() + seconds
    outer = _DEADLINE.get()
    token = _DEADLINE.set(end if outer is None else min(outer, end))
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def remaining() -> float | None:
    """Seconds left before the current deadline, or None outside one."""
    end = _DEADLINE.get()
    return None if end is None else end - time.monotonic()


def _clamp(timeout: float | None, kind: type[Exception]) -> float | None:
    left = remaining()
    if left is None:
        return timeout
    if left <= 0:
        raise kind("request deadline exceeded")
    return left if timeout is None else min(timeout, left)


class _Stream(httpcore.NetworkStream):
    def __init__(self, inner: httpcore.NetworkStream) -> None:
        self._inner = inner

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._inner.read(max_bytes, _clamp(timeout, httpcore.ReadTimeout))

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._inner.write(buffer, _clamp(timeout, httpcore.WriteTimeout))

    def close(self) -> None:
        self._inner.close()

    def start_tls(
        self, ssl_context: Any, server_hostname: str | None = None, timeout: float | None = None
    ) -> httpcore.NetworkStream:
        # httpcore passes the URL's host name here, so SNI and certificate checks
        # use it even when the TCP connection went to a pinned address.
        limit = _clamp(timeout, httpcore.ConnectTimeout)
        return _Stream(self._inner.start_tls(ssl_context, server_hostname, limit))

    def get_extra_info(self, info: str) -> Any:
        return self._inner.get_extra_info(info)


class Backend(httpcore.NetworkBackend):
    """httpcore's sync backend with the request deadline and optional address pinning."""

    def __init__(self, resolver: Resolver | None = None) -> None:
        self._sync = httpcore.SyncBackend()
        self._resolver = resolver

    def _connect(
        self, host: str, port: int, timeout: float | None, local: str | None, options: Any
    ) -> httpcore.NetworkStream:
        limit = _clamp(timeout, httpcore.ConnectTimeout)
        return _Stream(self._sync.connect_tcp(host, port, limit, local, options))

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        if self._resolver is None:
            return self._connect(host, port, timeout, local_address, socket_options)
        budget = _clamp(timeout, httpcore.ConnectTimeout)
        # The resolver validated every address; connect only to those, never to a
        # fresh lookup of `host`, so a rebinding DNS answer cannot slip in.
        addresses = self._resolver(host, port, float("inf") if budget is None else budget)
        failure: Exception = httpcore.ConnectError(f"no address for {host}")
        for address in addresses:
            try:
                return self._connect(address, port, timeout, local_address, socket_options)
            except httpcore.ConnectError as error:
                failure = error
        raise failure

    def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Any = None
    ) -> httpcore.NetworkStream:
        if self._resolver is not None:
            raise httpcore.ConnectError("unix sockets are not reachable in pinned mode")
        limit = _clamp(timeout, httpcore.ConnectTimeout)
        return _Stream(self._sync.connect_unix_socket(path, limit, socket_options))

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@cache
def _ssl_context() -> Any:
    return httpx.create_ssl_context()


class _Transport(httpx.HTTPTransport):
    """`HTTPTransport` (its exception mapping and response streams) over our backend.

    It replaces the parent's connection pool instead of calling the parent
    initializer, which would only build a pool without the backend.
    """

    def __init__(self, backend: Backend, proxy: str | None) -> None:
        common: dict[str, Any] = {
            "ssl_context": _ssl_context(),
            "max_connections": 100,  # httpx's default limits
            "max_keepalive_connections": 20,
            "keepalive_expiry": 5.0,
            "network_backend": backend,
        }
        if proxy is None:
            self._pool = httpcore.ConnectionPool(**common)
            return
        parsed = httpx.Proxy(proxy)
        if parsed.url.scheme not in ("http", "https"):
            raise ValueError(f"unsupported proxy scheme {parsed.url.scheme!r}; use http(s)")
        self._pool = httpcore.HTTPProxy(
            proxy_url=httpcore.URL(
                scheme=parsed.url.raw_scheme,
                host=parsed.url.raw_host,
                port=parsed.url.port,
                target=parsed.url.raw_path,
            ),
            proxy_auth=parsed.raw_auth,
            proxy_headers=parsed.headers.raw,
            proxy_ssl_context=parsed.ssl_context,
            **common,
        )


# --- proxy selection -----------------------------------------------------------------


def cidrs(no_proxy: str) -> list[tuple[Any, int]]:
    """The `addr/bits` entries of a NO_PROXY list."""
    nets = []
    for entry in no_proxy.split(","):
        net, slash, bits = entry.strip().partition("/")
        ip = validators.parse_ip(net)
        width = validators.parse_uint(bits, 0xFFFFFFFF) if slash else None
        if ip is not None and width is not None:
            nets.append((ip, width))
    return nets


def contains(net: Any, bits: int, ip: Any) -> bool:
    """Whether `ip` is inside `net/bits`; families never match each other."""
    if net.version != ip.version:
        return False
    width = 32 if net.version == 4 else 128
    if bits > width:
        return False
    diff = int(net) ^ int(ip)
    return bits == 0 or diff >> (width - bits) == 0


@dataclass(frozen=True)
class NoProxy:
    """NO_PROXY: `*`, host names (with their subdomains), addresses and CIDR blocks."""

    everything: bool
    hosts: tuple[str, ...]
    nets: tuple[tuple[Any, int], ...]

    @classmethod
    def parse(cls, value: str) -> NoProxy:
        hosts = []
        everything = False
        for entry in value.split(","):
            entry = entry.strip().lower()
            if entry == "*":
                everything = True
            elif entry and "/" not in entry:
                entry = entry.removeprefix("*").removeprefix(".").strip("[]").rstrip(".")
                if entry:
                    hosts.append(entry)
        return cls(everything, tuple(hosts), tuple(cidrs(value)))

    def bypass(self, host: str) -> bool:
        host = host.strip("[]").lower().rstrip(".")
        if self.everything:
            return True
        ip = validators.parse_ip(host)
        if ip is not None and any(contains(net, bits, ip) for net, bits in self.nets):
            return True
        return any(host == entry or host.endswith("." + entry) for entry in self.hosts)


def proxy_from(environ: Mapping[str, str]) -> str | None:
    """The first non-empty of ALL/HTTPS/HTTP_PROXY, used for every scheme."""
    for name in _PROXY_VARS:
        value = validators.trim(environ.get(name, ""))
        if value:
            return value if "://" in value else f"http://{value}"
    return None


# --- the client ----------------------------------------------------------------------


@dataclass(frozen=True)
class Response:
    """A fully read response: status, headers, final URL and the (decoded) body."""

    status: int
    headers: httpx.Headers
    url: str
    content: bytes

    @property
    def text(self) -> str:
        """The body as UTF-8, strictly."""
        return self.content.decode("utf-8")

    def json(self) -> Any:
        return jsonfmt.loads(self.content)


def read_capped(response: httpx.Response, max_bytes: int) -> bytes:
    """Read a streamed body, failing as soon as it exceeds `max_bytes` (decoded)."""
    declared = response.headers.get("content-length")
    if (
        declared is not None
        and declared.isdigit()
        and "content-encoding" not in response.headers
        and int(declared) > max_bytes
    ):
        raise BodyTooLarge(f"response body exceeds {max_bytes} bytes")
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > max_bytes:
            raise BodyTooLarge(f"response body exceeds {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


class Http:
    """Outbound HTTP with a whole-request `timeout` (seconds).

    `max_redirects` (default 10) applies to every request; pass
    `follow_redirects=False` for one that must not move. `environ` replaces
    `os.environ` for the proxy variables; `transport` replaces the network
    (tests pass `httpx.MockTransport`). With `resolver`, every connection goes
    direct to an address the resolver returned (see `netguard`) and proxies are
    ignored. Non-2xx statuses are returned, not raised. Safe to share between
    threads.
    """

    def __init__(
        self,
        timeout: float,
        *,
        max_redirects: int = 10,
        environ: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver | None = None,
        user_agent: str = USER_AGENT,
    ) -> None:
        env = os.environ if environ is None else environ
        self.timeout = float(timeout)
        self.max_redirects = max_redirects
        self._user_agent = user_agent
        self._transport = transport
        self._resolver = resolver
        pinned = resolver is not None or transport is not None
        self._proxy = None if pinned else proxy_from(env)
        no_proxy = env["NO_PROXY"] if "NO_PROXY" in env else env.get("no_proxy", "")
        self._no_proxy = NoProxy.parse(no_proxy)
        self._clients: dict[bool, httpx.Client] = {}
        self._lock = threading.Lock()

    def direct(self, url: str) -> bool:
        """Whether a request to `url` bypasses the proxy."""
        if self._proxy is None:
            return True
        try:
            host = httpx.URL(url).host
        except httpx.InvalidURL:
            return False
        return self._no_proxy.bypass(host)

    def _client(self, url: str) -> httpx.Client:
        direct = self.direct(url)
        with self._lock:
            client = self._clients.get(direct)
            if client is None:
                transport = self._transport or _Transport(
                    Backend(self._resolver), None if direct else self._proxy
                )
                client = httpx.Client(
                    transport=transport,
                    trust_env=False,
                    timeout=httpx.Timeout(self.timeout),
                    follow_redirects=self.max_redirects > 0,
                    max_redirects=self.max_redirects,
                    headers={"User-Agent": self._user_agent},
                )
                self._clients[direct] = client
            return client

    @contextmanager
    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
        content: bytes | str | None = None,
        json: Any = None,
        follow_redirects: bool | None = None,
    ) -> Iterator[httpx.Response]:
        """A streamed response; the deadline covers the block, so read the body inside it
        (with `read_capped`)."""
        merged = dict(headers or {})
        if json is not None:
            content = jsonfmt.dumps(json).encode("utf-8")
            merged.setdefault("Content-Type", "application/json; charset=utf-8")
        follow = self.max_redirects > 0 if follow_redirects is None else follow_redirects
        with deadline(self.timeout):
            with self._client(url).stream(
                method, url, headers=merged, params=params, content=content, follow_redirects=follow
            ) as response:
                yield response

    def request(
        self, method: str, url: str, *, max_bytes: int = MAX_BODY, **options: Any
    ) -> Response:
        """Send and read the whole body (at most `max_bytes`) within the timeout."""
        with self.stream(method, url, **options) as response:
            body = read_capped(response, max_bytes)
            return Response(response.status_code, response.headers, str(response.url), body)

    def get(self, url: str, **options: Any) -> Response:
        return self.request("GET", url, **options)

    def post(self, url: str, **options: Any) -> Response:
        return self.request("POST", url, **options)

    def close(self) -> None:
        with self._lock:
            clients, self._clients = list(self._clients.values()), {}
        for client in clients:
            client.close()

    def __enter__(self) -> Http:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

