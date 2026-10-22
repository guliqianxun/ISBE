import ipaddress
from collections.abc import Callable
from pathlib import Path

import pytest

from isbe.core import netguard

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture() -> Callable[[str], bytes]:
    """A frozen publisher excerpt's bytes (copied from src/sources/fixtures/article_dates)."""
    return lambda name: (FIXTURES / name).read_bytes()


@pytest.fixture
def local_fetcher() -> netguard.PublicFetcher:
    """The public-only fetcher, with every name pinned to the local test server."""

    def lookup(host: str, port: int, budget: float) -> list[str]:
        return ["127.0.0.1"]

    def loopback_ok(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return ip.is_loopback or netguard.public_ip(ip)

    resolver = netguard.PublicResolver(lookup=lookup, is_public=loopback_ok)
    return netguard.PublicFetcher(resolver=resolver)
