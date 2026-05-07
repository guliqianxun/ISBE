from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from isbe.facts.db import Base


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    topic_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # "weekly_digest" / "daily_digest" ...
    period_label: Mapped[str] = mapped_column(String(32))  # "2026-W19" / "2026-05-06"
    body_uri: Mapped[str] = mapped_column(String(512))  # MinIO key
    fingerprint: Mapped[dict] = mapped_column(JSONB)  # facts ids / memory revs / trace_id
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )


class TopicRun(Base):
    __tablename__ = "topic_runs"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    topic_id: Mapped[str] = mapped_column(String(64), index=True)
    flow_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))  # "ok" / "failed"
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
