"""Request guards that run before any data route: the Host allow-list, the CSRF token and JSON
content type on writes, the session cookie, and the client address used for
login throttling.

Client address. `auth.login` throttles per source, so the
source must be the real client. It is the TCP peer, unless the peer is a
configured trusted proxy: then it is the nearest `X-Forwarded-For` hop that
is not itself a trusted proxy. Nothing is trusted by default; set
`ISBE_TRUSTED_PROXIES` (comma-separated addresses or CIDR networks, e.g.
`127.0.0.1,10.0.0.0/8`) or pass `trusted_proxies` to `serve` when a reverse
proxy that overwrites or appends `X-Forwarded-For` (nginx
`$proxy_add_x_forwarded_for`) stands in front of the console.

Known limitation: the CSRF token is one value per process and `GET /api/session`
hands it to anyone; SameSite=Strict cookies, the custom header and the Host
allow-list make that sufficient today. A token per session would be stronger.
"""

import hmac
import ipaddress
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .wire import Reply, Request, json_reply

TRUSTED_PROXIES_ENV = "ISBE_TRUSTED_PROXIES"

type Address = ipaddress.IPv4Address | ipaddress.IPv6Address
type Network = ipaddress.IPv4Network | ipaddress.IPv6Network

# The bytes a URI authority may contain ('%' is handled apart).
_URI = frozenset(
    b"!#$&'()*+,-./0123456789:;=?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[]_abcdefghijklmnopqrstuvwxyz~"
)
_PORT = re.compile(r"\+?[0-9]+")


def authority(value: str) -> tuple[str, int | None] | None:
    """A Host header value as `(host, port)` (port None when absent), or None where it is
    not a valid URI authority. The host keeps IPv6 brackets."""
    raw = value.encode("latin-1", "replace")
    if not raw:
        return None
    colons, opened, closed, percent, at = 0, False, False, False, None
    end = len(raw)
    for i, b in enumerate(raw):
        if b in b"/?#":
            end = i
            break
        if b == ord(":"):
            if colons >= 8:
                return None
            colons += 1
        elif b == ord("["):
            if percent or opened:
                return None
            opened = True
        elif b == ord("]"):
            if not opened or closed:
                return None
            closed, colons, percent = True, 0, False
        elif b == ord("@"):
            at, colons, percent = i, 0, False
        elif b == ord("%"):
            percent = True
        elif b not in _URI:
            return None
    if opened != closed or colons > 1 or percent or end != len(raw):
        return None
    if end > 0 and at == end - 1:
        return None
    host_port = value.rsplit("@", 1)[-1]
    if host_port.startswith("["):
        host = host_port[: host_port.index("]") + 1]
    else:
        host = host_port.split(":", 1)[0]
    port = None
    if ":" in value:
        text = value[value.rindex(":") + 1 :]
        if _PORT.fullmatch(text) and int(text) <= 0xFFFF:
            port = int(text)
    return host, port


def _ip(text: str) -> Address | None:
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def loopback(ip: Address) -> bool:
    """Loopback addresses: 127.0.0.0/8 and `::1` only (not IPv4-mapped)."""
    if isinstance(ip, ipaddress.IPv6Address):
        return ip == ipaddress.IPv6Address(1)
    return ip.is_loopback


@dataclass(frozen=True)
class Access:
    """The process-wide CSRF token and, when bound to a loopback address, its port."""

    token: str
    local_port: int | None

    def allows_host(self, req: Request) -> bool:
        # Reject rebound hostnames even when Docker listens on a wildcard address.
        value = req.header("host")
        parsed = authority(value) if value is not None else None
        if parsed is None:
            return False
        host, port = parsed
        host = host.strip("[]")
        if self.local_port is not None and (80 if port is None else port) != self.local_port:
            return False
        if host.lower() == "localhost" and host.isascii():
            return True
        ip = _ip(host) if host.isascii() else None
        return ip is not None and (self.local_port is None or loopback(ip))

    def token_matches(self, value: str | None) -> bool:
        return value is not None and hmac.compare_digest(
            value.encode("latin-1", "replace"), self.token.encode("ascii")
        )

    def rejects(self, req: Request) -> Reply | None:
        if not self.allows_host(req):
            return json_reply(
                403,
                {"error": "Use localhost or the server IP address; reverse proxies must forward that Host"},
            )
        if req.method in ("POST", "PUT", "DELETE"):
            if not self.token_matches(req.header("x-isbe-csrf")):
                return json_reply(403, {"error": "Reload Settings before saving"})
            kind = req.header("content-type")
            if kind is None or kind.split(";", 1)[0].strip().lower() != "application/json":
                return json_reply(415, {"error": "Expected application/json"})
        return None


def session_token(req: Request) -> str:
    cookies = req.header("cookie") or ""
    for part in cookies.split(";"):
        part = part.strip()
        if part.startswith("isbe_session="):
            return part.removeprefix("isbe_session=")
    return ""


def cookie(token: str, secure: bool) -> str:
    """The `Set-Cookie` value: HttpOnly, SameSite=Strict, a day (or expired when clearing)."""
    age = 86400 if token else 0
    return (
        f"isbe_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}"
        + ("; Secure" if secure else "")
    )


@dataclass(frozen=True)
class TrustedProxies:
    networks: tuple[Network, ...] = ()

    @classmethod
    def parse(cls, text: str | Iterable[str] | None) -> TrustedProxies:
        """Addresses or CIDR networks, comma- or space-separated; raises ValueError on a bad entry."""
        if text is None:
            return cls()
        items = text.replace(",", " ").split() if isinstance(text, str) else list(text)
        networks = []
        for item in items:
            try:
                networks.append(ipaddress.ip_network(item.strip(), strict=False))
            except ValueError:
                raise ValueError(f"{TRUSTED_PROXIES_ENV}: not an address or network: {item!r}") from None
        return cls(tuple(networks))

    @classmethod
    def from_env(cls) -> TrustedProxies:
        return cls.parse(os.environ.get(TRUSTED_PROXIES_ENV))

    def __bool__(self) -> bool:
        return bool(self.networks)

    def __contains__(self, address: object) -> bool:
        ip = _ip(address) if isinstance(address, str) else None
        if ip is None:
            return False
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        return any(ip.version == n.version and ip in n for n in self.networks)


def _hop(text: str) -> str | None:
    """One `X-Forwarded-For` entry as an address: bare, `[v6]`, `[v6]:port` or `v4:port`."""
    text = text.strip()
    if text.startswith("[") and "]" in text:
        text = text[1 : text.index("]")]
    elif text.count(":") == 1:
        text = text.split(":", 1)[0]
    ip = _ip(text)
    return None if ip is None else str(ip)


def client_address(peer: str, forwarded: Sequence[str], trusted: TrustedProxies) -> str:
    """The client as `auth.login` should throttle it (see the module docstring)."""
    if not trusted or peer not in trusted:
        return peer
    hops = [hop for value in forwarded for hop in value.split(",") if hop.strip()]
    nearest = peer
    for hop in reversed(hops):
        address = _hop(hop)
        if address is None:
            # A hop the proxies did not write: what lies beyond it cannot be trusted.
            return nearest
        nearest = address
        if address not in trusted:
            return address
    return nearest
