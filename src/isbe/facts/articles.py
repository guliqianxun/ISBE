"""Generic articles table — RSS-ingested news for any topic.

Schema in alembic/versions/004_articles.py.
"""
from datetime import datetime

from sqlalchemy import ARRAY, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from isbe.facts.db import Base


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # sha1(source+url)
    topic_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(128))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    headline: Mapped[str] = mapped_column(String(1024))
    url: Mapped[str] = mapped_column(String(2048))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    lang: Mapped[str] = mapped_column(String(8), default="en")
