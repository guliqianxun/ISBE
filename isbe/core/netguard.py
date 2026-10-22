"""Public-only fetches of web- or user-supplied URLs.

Covers address classification (`public_ip`, `public_url`), the vetted-address
resolver (`PublicResolver`), the manual redirect loop and Cloudflare
DNS-over-HTTPS (when `ISBE_ARTICLE_DNS=cloudflare`).

Every address a host name resolves to must be public, or the whole answer is
refused, and the connection goes only to one of those vetted addresses: the
resolver runs inside the HTTP backend's `connect_tcp`, so there is no second
lookup a rebinding DNS server could answer differently. TLS still verifies the
certificate and sends SNI for the URL's host name. Redirects are followed by
hand, each hop revalidated, within one time budget; proxies are never used,
because a proxy would resolve the name itself.
"""

import ipaddress
import socket
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

import httpx

from isbe.core import http, jsonfmt, settings, validators

TIMEOUT = 8.0
MAX_REDIRECTS = 3
MAX_ADDRESSES = 16
DOH_ENDPOINT = "https://cloudflare-dns.com/dns-query"
DOH_MAX_BODY = 64 * 1024
_REDIRECTS = frozenset({301, 302, 303, 307, 308})

type Address = ipaddress.IPv4Address | ipaddress.IPv6Address
# (host, port, seconds left) -> candidate IP strings, not yet validated.
type Lookup = Callable[[str, int, float], Sequence[str]]


class Blocked(PermissionError):
    """The URL or one of its DNS answers is not a public address."""


class LookupFailed(OSError):
    """The host name has no usable address."""


class BadHost(ValueError):
    """A host name public DNS will not be asked about."""


class RedirectError(Exception):
    pass


def public_ip(ip: Address | str) -> bool:
    """Whether `ip` is a globally routable unicast address.

    IPv4 excludes this-network, private, loopback, CGNAT, link-local, the IETF
    protocol block 192.0.0.0/24, the documentation nets, 6to4 relay anycast,
    benchmarking, multicast and everything above. Only 192.0.0.0/24 and
    192.0.2.0/24 of 192.0.0.0/16 are special: 192.0.66.0/24 and 192.0.78.0/24
    host public sites. IPv6 allows only global unicast
    (2000::/3) minus 6to4, the 2001::/23 IETF block (Teredo, benchmarking, ORCHID),
    documentation 2001:db8::/32 and 3fff::/20; mapped, NAT64, ULA and link-local
    addresses are never public.
    """
    if isinstance(ip, str):
        ip = ipaddress.ip_address(ip)
    if ip.version == 4:
        a, b, c, _ = ip.packed
        return not (
            a in (0, 10, 127)
            or a >= 224
            or (a == 100 and 64 <= b <= 127)
            or (a == 169 and b == 254)
            or (a == 172 and 16 <= b <= 31)
            or (a == 192 and (b == 168 or (b == 0 and c in (0, 2)) or (b == 88 and c == 99)))
            or (a == 198 and (b in (18, 19) or (b == 51 and c == 100)))
            or (a == 203 and b == 0 and c == 113)
        )
    s0, s1 = int(ip) >> 112, (int(ip) >> 96) & 0xFFFF
    return (
        s0 & 0xE000 == 0x2000
        and s0 != 0x2002
        and not (s0 == 0x2001 and (s1 < 0x0200 or s1 == 0x0DB8))
        and not (s0 == 0x3FFF and s1 < 0x1000)
    )


def public_url(value: str) -> str:
    """A WHATWG-normalized http(s) URL whose literal IP host, if any, is public."""
    href = validators.validate_url(value)
    host = validators.whatwg(href).hostname.strip("[]")
    ip = validators.parse_ip(host)
    if ip is not None and not public_ip(ip):
        raise Blocked("original-page URL is not public")
    return href


def checked_addresses(
    addresses: Sequence[str], is_public: Callable[[Address], bool] = public_ip
) -> list[str]:
    """All of `addresses` if every one is public; one private answer refuses them all."""
    if not addresses:
        raise LookupFailed("host not found")
    parsed: list[str] = []
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise Blocked("original-page DNS contains a non-public address") from None
        if not is_public(ip):
            raise Blocked("original-page DNS contains a non-public address")
        parsed.append(str(ip))
    return parsed


def redirect_url(current: str, location: str, redirects: int, limit: int = MAX_REDIRECTS) -> str:
    """The next hop: resolved against `current`, fragment dropped, revalidated."""
    if redirects >= limit:
        raise RedirectError("original-page redirect limit reached")
    try:
        target = validators.whatwg(location, current)
    except ValueError:
        raise RedirectError("invalid original-page redirect") from None
    return public_url(target.href.split("#", 1)[0])


# --- resolution ---------------------------------------------------------------------


def valid_dns_name(host: str) -> bool:
    return len(host) <= 253 and all(
        label
        and len(label) <= 63
        and not label.startswith("-")
        and not label.endswith("-")
        and all(c.isascii() and (c.isalnum() or c == "-") for c in label)
        for label in host.split(".")
    )


def parse_doh(body: bytes) -> list[str]:
    """The distinct A records of a DNS JSON answer."""
    malformed = LookupFailed("public DNS returned malformed JSON")
    try:
        response = jsonfmt.loads(body)
    except ValueError:
        raise malformed from None
    if not isinstance(response, dict):
        raise malformed
    status = response.get("Status")
    truncated = response.get("TC", False)
    answers = response.get("Answer", [])

    def u16(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 0xFFFF

    if (
        not u16(status)
        or not isinstance(truncated, bool)
        or not isinstance(answers, list)
        or not all(
            isinstance(a, dict) and u16(a.get("type")) and isinstance(a.get("data"), str)
            for a in answers
        )
    ):
        raise malformed
    if status != 0:
        raise LookupFailed("host not found")
    if truncated:
        raise LookupFailed("public DNS returned a truncated answer")
    addresses: list[str] = []
    for answer in answers:
        if answer["type"] != 1:
            continue
        ip = validators.parse_ip(answer["data"])
        if ip is None or ip.version != 4:
            raise LookupFailed("public DNS returned an invalid A record")
        if str(ip) not in addresses:
            addresses.append(str(ip))
    if not addresses:
        raise LookupFailed("host not found")
    return addresses


def cloudflare(host: str, port: int, timeout: float, *, client: http.Http | None = None) -> list[str]:
    """A records from Cloudflare DNS-over-HTTPS; the caller must validate every one."""
    host = host.removesuffix(".")
    if not valid_dns_name(host):
        raise BadHost("invalid public DNS hostname")
    if timeout <= 0:
        raise TimeoutError("public DNS budget exhausted")
    # A records only; add AAAA when IPv6-only sources are needed.
    owned = client is None
    client = client or http.Http(timeout)
    try:
        response = client.get(
            DOH_ENDPOINT,
            params={"name": host, "type": "A"},
            headers={"Accept": "application/dns-json"},
            follow_redirects=False,
            max_bytes=DOH_MAX_BODY,
        )
    finally:
        if owned:
            client.close()
    if response.status != 200:
        raise LookupFailed(f"public DNS returned HTTP {response.status}")
    return parse_doh(response.content)


def system(host: str, port: int, timeout: float) -> list[str]:
    """getaddrinfo bounded by `timeout`; a stuck lookup is abandoned on its daemon thread."""
    outcome: list[object] = []
    done = threading.Event()

    def lookup() -> None:
        try:
            outcome.append(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError as error:
            outcome.append(error)
        finally:
            done.set()

    threading.Thread(target=lookup, name="isbe-dns", daemon=True).start()
    if not done.wait(max(0.0, timeout)):
        raise TimeoutError("original-page DNS lookup timed out")
    result = outcome[0]
    if isinstance(result, OSError):
        raise LookupFailed(f"host not found: {host}") from result
    return [str(info[4][0]) for info in result]  # type: ignore[attr-defined]


class PublicResolver:
    """The pinned resolver `http.Backend` calls for every connection.

    A literal address is checked as is; a name goes to Cloudflare when
    `ISBE_ARTICLE_DNS` (or `dns`) says so, else to the system resolver. Every
    answer is validated before the first 16 are handed back to connect to.
    """

    def __init__(
        self,
        *,
        lookup: Lookup | None = None,
        dns: str | None = None,
        is_public: Callable[[Address], bool] = public_ip,
        timeout: float = TIMEOUT,
    ) -> None:
        self._lookup = lookup
        self._dns = dns
        self._is_public = is_public
        self._timeout = timeout

    def __call__(self, host: str, port: int, budget: float) -> list[str]:
        host = host.strip("[]")
        if validators.parse_ip(host) is not None:
            addresses: Sequence[str] = [host]
        else:
            budget = min(budget, self._timeout)
            if budget <= 0:
                raise TimeoutError("original-page read timed out")
            lookup = self._lookup
            if lookup is None:
                dns = self._dns or settings.get("ISBE_ARTICLE_DNS")
                lookup = cloudflare if dns == "cloudflare" else system
            addresses = lookup(host, port, budget)
        return checked_addresses(addresses, self._is_public)[:MAX_ADDRESSES]


# --- fetching -----------------------------------------------------------------------


@dataclass(frozen=True)
class PublicResponse:
    """The final (non-redirect) hop; read its body inside the `stream` block."""

    url: str  # WHATWG form of the final URL
    response: httpx.Response

    @property
    def status(self) -> int:
        return self.response.status_code

    @property
    def headers(self) -> httpx.Headers:
        return self.response.headers

    def read(self, max_bytes: int) -> bytes:
        return http.read_capped(self.response, max_bytes)


def _visible_ascii(value: str) -> bool:
    return all(c == "\t" or " " <= c <= "~" for c in value)


class PublicFetcher:
    """GET public URLs: at most `max_redirects` revalidated redirects, `timeout` in total.

    `resolver` replaces the pinned public resolver (tests pass one with a
    stub lookup or a permissive `is_public`); the defaults are the
    original-page budgets (8 s, 3 redirects).
    """

    def __init__(
        self,
        *,
        timeout: float = TIMEOUT,
        max_redirects: int = MAX_REDIRECTS,
        resolver: PublicResolver | None = None,
        user_agent: str = http.USER_AGENT,
    ) -> None:
        self.timeout = timeout
        self.max_redirects = max_redirects
        self._resolver = resolver or PublicResolver(timeout=timeout)
        self._user_agent = user_agent

    @contextmanager
    def stream(
        self, url: str, *, headers: Mapping[str, str] | None = None
    ) -> Iterator[PublicResponse]:
        current = public_url(url)
        client = http.Http(
            self.timeout, max_redirects=0, resolver=self._resolver, user_agent=self._user_agent
        )
        try:
            with http.deadline(self.timeout):
                for hop in range(self.max_redirects + 1):
                    left = http.remaining()
                    if left is None or left <= 0:
                        raise TimeoutError("original-page read timed out")
                    with client.stream(
                        "GET", current, headers=headers, follow_redirects=False
                    ) as response:
                        if response.status_code in _REDIRECTS:
                            location = response.headers.get("location")
                            if location is None or not _visible_ascii(location):
                                raise RedirectError("original-page redirect has no location")
                            current = redirect_url(current, location, hop, self.max_redirects)
                            continue
                        yield PublicResponse(current, response)
                        return
                raise RedirectError("original-page redirect limit reached")
        finally:
            client.close()

    def fetch(
        self, url: str, *, max_bytes: int, headers: Mapping[str, str] | None = None
    ) -> http.Response:
        """The final response with its body (any status), read within the budget."""
        with self.stream(url, headers=headers) as page:
            body = page.read(max_bytes)
            return http.Response(page.status, page.headers, page.url, body)


def fetch_public(
    url: str,
    *,
    max_bytes: int,
    timeout: float = TIMEOUT,
    max_redirects: int = MAX_REDIRECTS,
    headers: Mapping[str, str] | None = None,
) -> http.Response:
    """One public-only GET with the default resolver; see `PublicFetcher`."""
    fetcher = PublicFetcher(timeout=timeout, max_redirects=max_redirects)
    return fetcher.fetch(url, max_bytes=max_bytes, headers=headers)
