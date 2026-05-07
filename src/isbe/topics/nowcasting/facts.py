from datetime import datetime

from sqlalchemy import ARRAY, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from isbe.facts.db import Base


class Paper(Base):
    __tablename__ = "papers"

    arxiv_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    authors: Mapped[list[str]] = mapped_column(ARRAY(String))
    abstract: Mapped[str] = mapped_column(Text)
    primary_category: Mapped[str] = mapped_column(String(32))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pdf_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_url: Mapped[str] = mapped_column(String(512))


class Repo(Base):
    __tablename__ = "repos"

    github_url: Mapped[str] = mapped_column(String(512), primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    stars: Mapped[int] = mapped_column(Integer, default=0)
    last_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_release_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    linked_paper_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # preprint / repo_update / blog_post / conf_accept
    type: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(64))
