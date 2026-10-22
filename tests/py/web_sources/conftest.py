import ipaddress
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from web_sources_helpers import console

from isbe.core import db, netguard
from isbe.web.console import Console


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A bare data directory, as the source API tests need it."""
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def conn(root: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(root / "isbe.db")
    yield connection
    connection.close()


@pytest.fixture
def app(root: Path, conn: sqlite3.Connection) -> Console:
    return console(root)


@pytest.fixture
def local_sites(monkeypatch: pytest.MonkeyPatch) -> None:
    """Public-only reads made on job threads reach 127.0.0.1 by any host name."""

    def allowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return ip.is_loopback or netguard.public_ip(ip)

    resolver = netguard.PublicResolver(lookup=lambda host, port, budget: ["127.0.0.1"], is_public=allowed)
    monkeypatch.setattr(netguard, "PublicResolver", lambda **options: resolver)
