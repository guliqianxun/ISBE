"""common facts

Revision ID: 001
Revises:
Create Date: 2026-05-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("topic_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("period_label", sa.String(32), nullable=False),
        sa.Column("body_uri", sa.String(512), nullable=False),
        sa.Column("fingerprint", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_artifacts_topic_id", "artifacts", ["topic_id"])
    op.create_table(
        "topic_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("topic_id", sa.String(64), nullable=False),
        sa.Column("flow_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=False),
    )
    op.create_index("ix_topic_runs_topic_id", "topic_runs", ["topic_id"])


def downgrade() -> None:
    op.drop_index("ix_topic_runs_topic_id", table_name="topic_runs")
    op.drop_table("topic_runs")
    op.drop_index("ix_artifacts_topic_id", table_name="artifacts")
    op.drop_table("artifacts")
