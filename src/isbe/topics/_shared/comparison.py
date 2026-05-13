"""Cross-topic period-over-period comparison.

Loads the most-recent prior artifact, diffs the current `fingerprint.facts`
buckets, and surfaces topic-specific extras (e.g. repos active since last
digest). Used by arxiv-weekly (papers + repos) and nvda-daily (news + filings).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from isbe.facts.artifacts import Artifact


@dataclass(frozen=True)
class BucketDelta:
    name: str         # fingerprint key, e.g. "papers" / "news" / "filings"
    label: str        # human-readable label, e.g. "论文"
    current_n: int
    prior_n: int
    delta_n: int
    new: list[str]
    carried: list[str]
    dropped: list[str]


def load_prior_artifact(session, topic_id: str, current_period: str) -> Artifact | None:
    """Most recent artifact for this topic from an *earlier* period (strict <).

    ISO period labels (`2026-W19`, `2026-05-07`) compare correctly lexicographically.
    """
    return session.scalars(
        select(Artifact)
        .where(Artifact.topic_id == topic_id, Artifact.period_label < current_period)
        .order_by(Artifact.period_label.desc())
        .limit(1)
    ).first()


def build_comparison(
    prior: Artifact | None,
    current_buckets: dict[str, set[str]],
    *,
    bucket_labels: dict[str, str],
    compare_label: str,
    repos: list | None = None,
) -> dict | None:
    """Diff `current_buckets` against `prior.fingerprint.facts.<bucket>` sets.

    `compare_label` shows up in the template header ("上周对比"/"上日对比").
    `repos`, if given, gets filtered to those with `last_commit_at > prior.created_at`
    — used by arxiv-weekly to surface "active since last digest". Returns None
    when there is no prior artifact yet.
    """
    if prior is None:
        return None

    prior_facts = prior.fingerprint.get("facts") or {}
    buckets: list[BucketDelta] = []
    for name, current_set in current_buckets.items():
        prior_set = set(prior_facts.get(name) or [])
        buckets.append(
            BucketDelta(
                name=name,
                label=bucket_labels.get(name, name),
                current_n=len(current_set),
                prior_n=len(prior_set),
                delta_n=len(current_set) - len(prior_set),
                new=sorted(current_set - prior_set),
                carried=sorted(current_set & prior_set),
                dropped=sorted(prior_set - current_set),
            )
        )

    active_repos = []
    if repos:
        threshold = prior.created_at
        for r in repos:
            if r.last_commit_at and r.last_commit_at > threshold:
                active_repos.append(r)

    return {
        "prior_period": prior.period_label,
        "prior_artifact_id": str(prior.id),
        "compare_label": compare_label,
        "buckets": buckets,
        "active_repos": active_repos,
    }
