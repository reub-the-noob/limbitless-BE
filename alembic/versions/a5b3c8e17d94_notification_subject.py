"""add notification subject_type / subject_id

A logical (type, id) pointer to what a notification is about — same shape
as audit_log_entries. Lets a superseded appointment reminder be found
and deleted without a JSON query, and lets the FE deep-link a row.

Revision ID: a5b3c8e17d94
Revises: c7a1d9e40b28
Create Date: 2026-09-07
"""

import sqlalchemy as sa
from alembic import op

revision = "a5b3c8e17d94"
down_revision = "c7a1d9e40b28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("subject_type", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "notifications", sa.Column("subject_id", sa.Integer(), nullable=True)
    )
    op.create_index(
        "ix_notifications_subject_type", "notifications", ["subject_type"]
    )
    op.create_index(
        "ix_notifications_subject_id", "notifications", ["subject_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_subject_id", table_name="notifications")
    op.drop_index("ix_notifications_subject_type", table_name="notifications")
    op.drop_column("notifications", "subject_id")
    op.drop_column("notifications", "subject_type")
