"""`radar status` — 各 active topic 一眼看到当下状态。

读 Postgres 的 topic_runs / artifacts + 本地 mirror 目录，不发任何外部请求。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer
from sqlalchemy import func, select

from isbe.facts.artifacts import Artifact, TopicRun
from isbe.facts.db import make_session_factory
from isbe.scheduler import _FLOW_DISPATCH
from isbe.topics.registry import default_topics_root, discover_topics


def _classify_flows() -> tuple[set[str], set[str]]:
    """Derive {collect,digest} flow-name buckets from scheduler._FLOW_DISPATCH.

    Suffix convention from @flow(name=...): `*-collector` / `*-download-pdfs`
    → collect; `*-digester` → digest. Unknown suffixes silently ignored
    (no topic surface for them anyway).
    """
    collect: set[str] = set()
    digest: set[str] = set()
    for flow_fn, _ in _FLOW_DISPATCH.values():
        name = flow_fn.name
        if name.endswith("-collector") or name.endswith("-download-pdfs"):
            collect.add(name)
        elif name.endswith("-digester"):
            digest.add(name)
    return collect, digest


_COLLECT_FLOWS, _DIGEST_FLOWS = _classify_flows()


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _fmt_dt(dt: datetime | None) -> str:
    dt = _aware(dt)
    if dt is None:
        return "-"
    return dt.strftime("%m-%d %H:%M")


def _fmt_age(dt: datetime | None) -> str:
    dt = _aware(dt)
    if dt is None:
        return "-"
    secs = int((datetime.now(UTC) - dt).total_seconds())
    if secs < 0:
        return "future"
    if secs < 3600:
        return f"{max(secs // 60, 0)}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def _latest_local_artifact(topic_id: str, mirror_root: Path) -> Path | None:
    d = mirror_root / topic_id
    if not d.exists():
        return None
    candidates: list[Path] = []
    for sub in d.iterdir():
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        candidates.extend(p for p in sub.glob("*.md") if p.name != "latest.md")
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def status(
    days: int = typer.Option(7, "--days", help="failed-run window in days"),
    mirror: Path = typer.Option(
        Path("artifacts"), "--mirror", help="local artifact mirror root"
    ),
) -> None:
    """Show one-row-per-topic snapshot: last collect / digest / artifact / recent failures."""
    topics = [t for t in discover_topics(default_topics_root()) if t.active]
    if not topics:
        typer.echo("(no active topics)")
        return

    Session = make_session_factory()
    cutoff = datetime.now(UTC) - timedelta(days=days)

    rows = []
    with Session() as s:
        for t in topics:
            last_collect = s.scalars(
                select(TopicRun)
                .where(TopicRun.topic_id == t.id, TopicRun.flow_name.in_(_COLLECT_FLOWS))
                .order_by(TopicRun.started_at.desc())
                .limit(1)
            ).first()
            last_digest = s.scalars(
                select(TopicRun)
                .where(TopicRun.topic_id == t.id, TopicRun.flow_name.in_(_DIGEST_FLOWS))
                .order_by(TopicRun.started_at.desc())
                .limit(1)
            ).first()
            fail_count = s.scalar(
                select(func.count())
                .select_from(TopicRun)
                .where(
                    TopicRun.topic_id == t.id,
                    TopicRun.status == "failed",
                    TopicRun.started_at >= cutoff,
                )
            ) or 0
            last_art = s.scalars(
                select(Artifact)
                .where(Artifact.topic_id == t.id)
                .order_by(Artifact.created_at.desc())
                .limit(1)
            ).first()
            local_path = _latest_local_artifact(t.id, mirror)
            rows.append((t, last_collect, last_digest, last_art, fail_count, local_path))

    fail_header = f"FAIL/{days}d"
    typer.echo(
        f"{'TOPIC':18}{'CADENCE':20}{'COLLECT':18}{'DIGEST':18}"
        f"{'ARTIFACT':22}{fail_header:>8}"
    )
    typer.echo("-" * 104)
    for t, c, d, a, fc, local in rows:
        collect_cell = f"{_fmt_dt(c.started_at)} {c.status}" if c else "-"
        digest_cell = f"{_fmt_dt(d.started_at)} {d.status}" if d else "-"
        if a:
            art_cell = f"{a.period_label} ({_fmt_age(a.created_at)} ago)"
        elif local:
            art_cell = f"{local.parent.name} (fs only)"
        else:
            art_cell = "-"
        typer.echo(
            f"{t.id:18}{t.cadence:20}{collect_cell:18}{digest_cell:18}"
            f"{art_cell:22}{fc:>8}"
        )
    typer.echo("")
    typer.echo("hint: artifacts/<topic>/<period>/latest.md  is always the freshest render")
