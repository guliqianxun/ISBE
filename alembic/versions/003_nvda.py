"""nvda facts (prices/news/sec)

Revision ID: 003
Revises: 002
"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prices_daily",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("trade_date", sa.Date, primary_key=True),
        sa.Column("open", sa.Float, nullable=False),
        sa.Column("high", sa.Float, nullable=False),
        sa.Column("low", sa.Float, nullable=False),
        sa.Column("close", sa.Float, nullable=False),
        sa.Column("volume", sa.BigInteger, nullable=False),
        sa.Column("adj_close", sa.Float, nullable=False),
    )
    op.create_table(
        "news_items",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("headline", sa.String(512), nullable=False),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("tickers", sa.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("lang", sa.String(8), nullable=False, server_default="en"),
    )
    op.create_index("ix_news_items_source", "news_items", ["source"])
    op.create_index("ix_news_items_published_at", "news_items", ["published_at"])
    op.create_table(
        "sec_filings",
        sa.Column("accession_no", sa.String(32), primary_key=True),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("form_type", sa.String(16), nullable=False),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body_url", sa.String(1024), nullable=False),
    )
    op.create_index("ix_sec_filings_ticker", "sec_filings", ["ticker"])
    op.create_index("ix_sec_filings_form_type", "sec_filings", ["form_type"])
    op.create_index("ix_sec_filings_filed_at", "sec_filings", ["filed_at"])


def downgrade() -> None:
    op.drop_table("sec_filings")
    op.drop_table("news_items")
    op.drop_table("prices_daily")
