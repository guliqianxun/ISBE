import os

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


def make_session_factory(engine=None):
    return sessionmaker(bind=engine or make_engine(), expire_on_commit=False, future=True)
