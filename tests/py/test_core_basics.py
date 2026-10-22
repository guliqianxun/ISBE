import re

import pytest

from isbe.core import assets, db, settings


def test_now_matches_the_stored_timestamp_format() -> None:
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", db.now())


def test_sha256_is_lowercase_hex_of_utf8() -> None:
    assert db.sha256("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert db.sha256("主题") == db.sha256("主题".encode().decode())


def test_task_context_is_scoped() -> None:
    assert db.current_task() is None
    with db.task("t1"):
        assert db.current_task() == "t1"
        with db.task("t2"):
            assert db.current_task() == "t2"
        assert db.current_task() == "t1"
    assert db.current_task() is None


def test_environment_beats_saved_and_task_scope_beats_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "_saved", {"ESSAIES_URL": "http://file", "GITHUB_TOKEN": "f"})
    assert settings.get("ESSAIES_URL") == "http://file"
    monkeypatch.setenv("ESSAIES_URL", "http://env")
    monkeypatch.setenv("GITHUB_TOKEN", "   ")
    assert settings.get("ESSAIES_URL") == "http://env"
    assert settings.get("GITHUB_TOKEN") == "f", "blank environment values do not override"
    with settings.task_scope({"ESSAIES_URL": "http://task"}):
        assert settings.get("ESSAIES_URL") == "http://task"
        assert settings.get("GITHUB_TOKEN") is None, "the task snapshot is complete for known keys"
    assert settings.get("ESSAIES_URL") == "http://env"


def test_shared_assets_resolve_from_the_checkout() -> None:
    names = sorted(p.name for p in assets.path("migrations").glob("*.sql"))
    assert names[0] == "001_init.sql" and len(names) >= 10
    assert "digest.md" in {p.name for p in assets.path("prompts", "zh").iterdir()}
    assert assets.text("web", "index.html").lstrip().lower().startswith("<!doctype html")
