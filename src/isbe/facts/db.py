import os
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


def db_url() -> str:
    user = os.getenv("POSTGRES_USER", "isbe")
    pw = os.getenv("POSTGRES_PASSWORD", "changeme")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "isbe")
    return f"postgresql+psycopg://{user}:{pw}@{host}:{port}/{db}"


class Base(DeclarativeBase):
    pass


metadata = Base.metadata


def make_engine():
    return create_engine(db_url(), future=True)


@lru_cache(maxsize=8)
def _engine_for(url: str):
    return create_engine(url, future=True)


def get_engine():
    """Process-wide engine, cached per DB URL.

    Every engine owns a connection pool; creating one per session factory call
    (the old behavior) leaked pools from loops like per-feed RSS collection.
    Keyed on the URL so tests that swap POSTGRES_* env vars get a fresh engine.
    """
    return _engine_for(db_url())


def make_session_factory(engine=None):
    return sessionmaker(bind=engine or get_engine(), expire_on_commit=False, future=True)
