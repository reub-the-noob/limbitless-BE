"""add notifications.emailed_at

Tracks whether a notification has been delivered by email (requirements
Section 5.6). NULL = not yet sent; the dispatch pass stamps it on a
successful send and leaves it NULL on failure so the next pass retries.

Revision ID: b8d2f5a1c907
Revises: a5b3c8e17d94
Create Date: 2026-09-07
"""

import sqlalchemy as sa
from alembic import op

revision = "b8d2f5a1c907"
down_revision = "a5b3c8e17d94"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notifications", sa.Column("emailed_at", sa.DateTime(), nullable=True)
    )
    op.create_index(
        "ix_notifications_emailed_at", "notifications", ["emailed_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_emailed_at", table_name="notifications")
    op.drop_column("notifications", "emailed_at")
