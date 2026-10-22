import ipaddress
from collections.abc import Callable
from typing import Any

import pytest

from isbe.core import netguard


def loopback_ok(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.is_loopback or netguard.public_ip(ip)


@pytest.fixture
def local_fetcher() -> Callable[..., netguard.PublicFetcher]:
    """A public-only fetcher whose host names all resolve to 127.0.0.1 (allowed)."""

    def make(**options: Any) -> netguard.PublicFetcher:
        resolver = netguard.PublicResolver(
            lookup=lambda host, port, budget: ["127.0.0.1"], is_public=loopback_ok
        )
        return netguard.PublicFetcher(resolver=resolver, **options)

    return make


@pytest.fixture
def local_resolver() -> netguard.PublicResolver:
    """The public-only resolver, with every host name answering 127.0.0.1 (allowed)."""
    return netguard.PublicResolver(lookup=lambda host, port, budget: ["127.0.0.1"], is_public=loopback_ok)
