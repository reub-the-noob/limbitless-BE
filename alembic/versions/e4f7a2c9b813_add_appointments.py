"""add appointments and practice cancellation notice window

Second slice of Section 5.9/5.10 (appointment scheduling): the booked
visit itself, plus the practice-configurable cancellation notice window
Section 5.10 needs. ``CoverageDetermination`` and reschedule follow in a
later migration.

Revision ID: e4f7a2c9b813
Revises: d6f3b8a2c541
Create Date: 2026-09-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e4f7a2c9b813"
down_revision = "d6f3b8a2c541"
branch_labels = None
depends_on = None

_APPOINTMENT_STATUSES = (
    "booked",
    "cancelled_by_patient",
    "cancelled_by_practitioner",
    "no_show",
)


def upgrade() -> None:
    op.add_column(
        "practices",
        sa.Column(
            "cancellation_notice_hours",
            sa.Integer(),
            server_default="24",
            nullable=False,
        ),
    )

    bind = op.get_bind()
    sa.Enum(*_APPOINTMENT_STATUSES, name="appointment_status").create(
        bind, checkfirst=True
    )
    # create_type=False: the type is already created above: op.create_table's
    # DDL compiler would otherwise re-issue CREATE TYPE and fail with
    # DuplicateObject (see d6f3b8a2c541 for the same gotcha).
    appointment_status = postgresql.ENUM(
        *_APPOINTMENT_STATUSES, name="appointment_status", create_type=False
    )
    appointment_type = postgresql.ENUM(
        "initial_assessment",
        "fitting",
        "review",
        "adjustment",
        "follow_up",
        "other",
        name="appointment_type",
        create_type=False,
    )

    op.create_table(
        "appointments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("practice_id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=True),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("practitioner_id", sa.Integer(), nullable=False),
        sa.Column("slot_id", sa.Integer(), nullable=True),
        sa.Column("appointment_type", appointment_type, nullable=False),
        sa.Column("scheduled_start", sa.DateTime(), nullable=False),
        sa.Column("scheduled_end", sa.DateTime(), nullable=False),
        sa.Column(
            "status",
            appointment_status,
            nullable=False,
            server_default="booked",
        ),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column(
            "late_cancellation",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["practice_id"], ["practices.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patients.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["practitioner_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["slot_id"], ["availability_slots.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_appointments_practice_id", "appointments", ["practice_id"]
    )
    op.create_index("ix_appointments_site_id", "appointments", ["site_id"])
    op.create_index(
        "ix_appointments_patient_id", "appointments", ["patient_id"]
    )
    op.create_index(
        "ix_appointments_practitioner_id", "appointments", ["practitioner_id"]
    )
    op.create_index("ix_appointments_slot_id", "appointments", ["slot_id"])
    op.create_index(
        "ix_appointments_scheduled_start", "appointments", ["scheduled_start"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_appointments_scheduled_start", table_name="appointments"
    )
    op.drop_index("ix_appointments_slot_id", table_name="appointments")
    op.drop_index("ix_appointments_practitioner_id", table_name="appointments")
    op.drop_index("ix_appointments_patient_id", table_name="appointments")
    op.drop_index("ix_appointments_site_id", table_name="appointments")
    op.drop_index("ix_appointments_practice_id", table_name="appointments")
    op.drop_table("appointments")

    bind = op.get_bind()
    sa.Enum(name="appointment_status").drop(bind, checkfirst=True)

    op.drop_column("practices", "cancellation_notice_hours")
