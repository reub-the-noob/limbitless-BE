"""appointment reschedule support

Section 5.10: reschedule is one atomic action (release the old slot,
take a new one), not a cancel - so it gets its own ``rescheduled``
appointment status, distinct from ``cancelled_by_*``, plus a link
between the old and new appointment rows.

Revision ID: f8a5c1e7d234
Revises: e4f7a2c9b813
Create Date: 2026-09-05
"""

import sqlalchemy as sa
from alembic import op

revision = "f8a5c1e7d234"
down_revision = "e4f7a2c9b813"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE appointment_status ADD VALUE IF NOT EXISTS 'rescheduled'"
    )
    op.add_column(
        "appointments",
        sa.Column(
            "rescheduled_to_id",
            sa.Integer(),
            sa.ForeignKey("appointments.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "appointments",
        sa.Column(
            "rescheduled_from_id",
            sa.Integer(),
            sa.ForeignKey("appointments.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_appointments_rescheduled_to_id",
        "appointments",
        ["rescheduled_to_id"],
    )
    op.create_index(
        "ix_appointments_rescheduled_from_id",
        "appointments",
        ["rescheduled_from_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_appointments_rescheduled_from_id", table_name="appointments"
    )
    op.drop_index(
        "ix_appointments_rescheduled_to_id", table_name="appointments"
    )
    op.drop_column("appointments", "rescheduled_from_id")
    op.drop_column("appointments", "rescheduled_to_id")
    # Postgres has no "DROP VALUE" for an enum label; harmless if unused.
