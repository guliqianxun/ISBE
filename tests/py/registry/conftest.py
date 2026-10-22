import ipaddress
import sqlite3
from collections.abc import Iterator

import pytest

from isbe.core import db, netguard


def loopback_ok(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.is_loopback or netguard.public_ip(ip)


@pytest.fixture
def local_resolver() -> netguard.PublicResolver:
    """The public-only resolver, with every host name answering 127.0.0.1 (allowed)."""
    return netguard.PublicResolver(lookup=lambda host, port, budget: ["127.0.0.1"], is_public=loopback_ok)


@pytest.fixture
def local_sources(monkeypatch: pytest.MonkeyPatch, local_resolver: netguard.PublicResolver) -> None:
    """Source reads made without an explicit resolver (job threads) reach 127.0.0.1 by name."""
    monkeypatch.setattr(netguard, "PublicResolver", lambda **options: local_resolver)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    yield connection
    connection.close()
