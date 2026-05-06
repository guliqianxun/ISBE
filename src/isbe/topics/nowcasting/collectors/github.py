"""github collector — tracks a hardcoded list of nowcasting-related repos."""

from datetime import datetime, timezone

import httpx
from prefect import flow, task
from sqlalchemy.orm import Session

from isbe.facts.db import make_session_factory
from isbe.topics.nowcasting.facts import Repo

TRACKED_REPOS: list[str] = [
    # owner/name; expand by editing this list (P3 makes it config)
    "openclimatefix/skillful_nowcasting",
    "google-research/google-research",  # placeholder; many subdirs
]


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def fetch_repo_meta(owner_repo: str) -> Repo:
    """GET https://api.github.com/repos/<owner>/<name> → Repo (not attached)."""
    resp = httpx.get(f"https://api.github.com/repos/{owner_repo}", timeout=30.0)
    resp.raise_for_status()
    data = resp.json()
    return Repo(
        github_url=data["html_url"],
        title=data["name"],
        description=data.get("description"),
        stars=int(data["stargazers_count"]),
        last_commit_at=_parse_iso(data["pushed_at"]) if data.get("pushed_at") else None,
        last_release_at=None,  # release endpoint is separate; skip in MVP
        linked_paper_ids=[],
    )


def upsert_repo(session: Session, repo: Repo) -> bool:
    """Insert or update. Returns True if inserted, False if updated."""
    existing = session.get(Repo, repo.github_url)
    if existing is None:
        session.add(repo)
        session.commit()
        return True
    existing.title = repo.title
    existing.description = repo.description
    existing.stars = repo.stars
    existing.last_commit_at = repo.last_commit_at
    session.commit()
    return False


@task
def fetch_one(owner_repo: str) -> Repo:
    return fetch_repo_meta(owner_repo)


@flow(name="github-collector")
def github_collector() -> int:
    Session = make_session_factory()
    n_new = 0
    with Session() as s:
        for owner_repo in TRACKED_REPOS:
            try:
                r = fetch_one(owner_repo)
                if upsert_repo(s, r):
                    n_new += 1
            except httpx.HTTPError as e:
                print(f"skip {owner_repo}: {e}")
    return n_new


if __name__ == "__main__":
    print(github_collector())
