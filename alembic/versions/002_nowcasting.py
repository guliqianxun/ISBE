"""nowcasting facts

Revision ID: 002
Revises: 001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "papers",
        sa.Column("arxiv_id", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("authors", sa.ARRAY(sa.String), nullable=False),
        sa.Column("abstract", sa.Text, nullable=False),
        sa.Column("primary_category", sa.String(32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pdf_uri", sa.String(512), nullable=True),
        sa.Column("source_url", sa.String(512), nullable=False),
    )
    op.create_index("ix_papers_submitted_at", "papers", ["submitted_at"])
    op.create_table(
        "repos",
        sa.Column("github_url", sa.String(512), primary_key=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("stars", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_commit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_release_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("linked_paper_ids", sa.ARRAY(sa.String), nullable=False, server_default="{}"),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
    )
    op.create_index("ix_events_type", "events", ["type"])
    op.create_index("ix_events_observed_at", "events", ["observed_at"])


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("repos")
    op.drop_table("papers")
