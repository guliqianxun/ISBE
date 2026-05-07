from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from isbe.topics.nowcasting.collectors.github import (
    TRACKED_REPOS,
    fetch_repo_meta,
    upsert_repo,
)
from isbe.topics.nowcasting.facts import Repo


def test_tracked_repos_nonempty():
    assert len(TRACKED_REPOS) >= 1


def test_fetch_repo_meta_parses_response():
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "html_url": "https://github.com/foo/bar",
        "name": "bar",
        "description": "Nowcasting toy",
        "stargazers_count": 42,
        "pushed_at": "2026-05-01T10:00:00Z",
    }
    with patch("isbe.topics.nowcasting.collectors.github.httpx.get", return_value=fake_resp):
        repo = fetch_repo_meta("foo/bar")
    assert isinstance(repo, Repo)
    assert repo.stars == 42
    assert repo.last_commit_at == datetime(2026, 5, 1, 10, 0, tzinfo=UTC)


def test_upsert_repo_inserts_or_updates():
    session = MagicMock()
    session.get.return_value = None  # not exist
    r = Repo(
        github_url="https://github.com/foo/bar",
        title="bar",
        description=None,
        stars=10,
        last_commit_at=datetime(2026, 5, 1, tzinfo=UTC),
        last_release_at=None,
        linked_paper_ids=[],
    )
    inserted = upsert_repo(session, r)
    assert inserted is True
    session.add.assert_called_once_with(r)
