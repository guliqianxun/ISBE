"""papers.fulltext_uri — pointer to metrail-extracted markdown.

Stored relative to the papers mirror root (ISBE_PAPERS_MIRROR), because the
mirror root differs between host and container.

Revision ID: 005
Revises: 004
"""

import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("papers", sa.Column("fulltext_uri", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("papers", "fulltext_uri")
