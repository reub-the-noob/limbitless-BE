"""add notifications

In-app notifications (requirements Section 5.6): one row per message per
recipient user. ``deliver_at`` carries the time-based kinds (appointment
reminder, milestone due/overdue) without a scheduler — the list endpoint
hides a row until its ``deliver_at`` has passed.

Revision ID: c7a1d9e40b28
Revises: d3f8a6c2b917
Create Date: 2026-09-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c7a1d9e40b28"
down_revision = "d3f8a6c2b917"
branch_labels = None
depends_on = None

_NOTIFICATION_TYPES = (
    "appointment_booked",
    "appointment_cancelled",
    "appointment_rescheduled",
    "appointment_reminder",
    "coverage_decided",
    "prom_flagged",
    "patient_reassigned",
)


def upgrade() -> None:
    bind = op.get_bind()
    sa.Enum(*_NOTIFICATION_TYPES, name="notification_type").create(
        bind, checkfirst=True
    )
    # create_type=False: the type is created above - op.create_table's DDL
    # compiler would otherwise re-issue CREATE TYPE and fail with
    # DuplicateObject (see d3f8a6c2b917 / d6f3b8a2c541 for the same gotcha).
    notification_type = postgresql.ENUM(
        *_NOTIFICATION_TYPES, name="notification_type", create_type=False
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("type", notification_type, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("deliver_at", sa.DateTime(), nullable=True),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notifications_user_id", "notifications", ["user_id"]
    )
    op.create_index(
        "ix_notifications_deliver_at", "notifications", ["deliver_at"]
    )
    op.create_index(
        "ix_notifications_created_at", "notifications", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_created_at", table_name="notifications")
    op.drop_index("ix_notifications_deliver_at", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")

    bind = op.get_bind()
    sa.Enum(name="notification_type").drop(bind, checkfirst=True)
