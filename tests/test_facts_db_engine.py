"""Unit tests for the process-wide engine cache (no DB connection needed —
engines connect lazily)."""

from isbe.facts.db import _engine_for, get_engine, make_engine


def test_get_engine_is_singleton_per_url(monkeypatch):
    _engine_for.cache_clear()
    monkeypatch.setenv("POSTGRES_DB", "isbe_test_a")
    assert get_engine() is get_engine()


def test_get_engine_new_engine_on_url_change(monkeypatch):
    _engine_for.cache_clear()
    monkeypatch.setenv("POSTGRES_DB", "isbe_test_a")
    engine_a = get_engine()
    monkeypatch.setenv("POSTGRES_DB", "isbe_test_b")
    engine_b = get_engine()
    assert engine_a is not engine_b


def test_make_engine_is_uncached():
    assert make_engine() is not make_engine()
