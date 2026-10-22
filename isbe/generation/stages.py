"""The four pipeline stages a queued report runs, as replaceable callables.

`Stages()` holds the real ones; tests pass their own to drive the queue
without the network. The worker wraps every call in `db.job`, so a fake
still writes the `runs` ledger a real stage would. The real stages are
imported when they run, so this package loads without them.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from isbe.core import settings
from isbe.core.llm import Llm
from isbe.topics import Topic

type Stats = dict[str, Any]


@dataclass(frozen=True)
class Endpoints:
    """Where collection gets papers: essaies when set, else the arXiv API.
    The same fields as `isbe.collect.Endpoints`, read from the task's settings."""

    essaies: str | None
    arxiv_api: str
    github_api: str

    @classmethod
    def from_settings(cls) -> Endpoints:
        return cls(
            essaies=settings.get("ESSAIES_URL"),
            arxiv_api=settings.get("ARXIV_API_URL") or settings.ARXIV_API,
            github_api=settings.get("GITHUB_API_URL") or settings.GITHUB_API,
        )


# collect(conn, topic, endpoints, stats)
type Collect = Callable[[sqlite3.Connection, Topic, Endpoints, Stats], None]
# triage(conn, llm, run_id, topic, stats)
type Triage = Callable[[sqlite3.Connection, Llm, str, Topic, Stats], None]
# enrich(conn, llm, run_id, topic, data, essaies, stats)
type Enrich = Callable[[sqlite3.Connection, Llm, str, Topic, Path, str | None, Stats], None]
# digest(conn, llm, run_id, topic, data, stats); must set stats["report"]
type Digest = Callable[[sqlite3.Connection, Llm, str, Topic, Path, Stats], None]


def collect(conn: sqlite3.Connection, topic: Topic, endpoints: Endpoints, stats: Stats) -> None:
    from isbe.collect import Endpoints as Sources
    from isbe.collect import collect as run

    sources = Sources(
        essaies=endpoints.essaies, arxiv_api=endpoints.arxiv_api, github_api=endpoints.github_api
    )
    run(conn, topic, sources, stats)


def triage(conn: sqlite3.Connection, llm: Llm, run_id: str, topic: Topic, stats: Stats) -> None:
    from isbe.pipeline import triage as stage

    stage.run(conn, llm, run_id, topic, stats)


def enrich(
    conn: sqlite3.Connection,
    llm: Llm,
    run_id: str,
    topic: Topic,
    data: Path,
    essaies: str | None,
    stats: Stats,
) -> None:
    from isbe.pipeline import enrich as stage

    stage.run(conn, llm, run_id, topic, data, essaies, stats)


def digest(
    conn: sqlite3.Connection, llm: Llm, run_id: str, topic: Topic, data: Path, stats: Stats
) -> None:
    from isbe.pipeline import digest as stage

    stage.run(conn, llm, run_id, topic, data, stats)


@dataclass(frozen=True)
class Stages:
    collect: Collect = collect
    triage: Triage = triage
    enrich: Enrich = enrich
    digest: Digest = digest
