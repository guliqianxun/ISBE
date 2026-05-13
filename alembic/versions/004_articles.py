"""generic articles table (RSS-ingested news for any topic)

Revision ID: 004
Revises: 003
"""
from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "articles",
        sa.Column("id", sa.String(40), primary_key=True),  # sha1(source + url)
        sa.Column("topic_id", sa.String(64), nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("headline", sa.String(1024), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("summary", sa.Text, nullable=True),  # RSS description / excerpt
        sa.Column("tags", sa.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("lang", sa.String(8), nullable=False, server_default="en"),
    )
    op.create_index("ix_articles_topic_id", "articles", ["topic_id"])
    op.create_index("ix_articles_published_at", "articles", ["published_at"])


def downgrade() -> None:
    op.drop_index("ix_articles_published_at", table_name="articles")
    op.drop_index("ix_articles_topic_id", table_name="articles")
    op.drop_table("articles")
