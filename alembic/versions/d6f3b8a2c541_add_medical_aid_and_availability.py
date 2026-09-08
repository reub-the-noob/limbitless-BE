"""add medical_aid_memberships and availability_slots

First slice of Section 5.9 (appointment scheduling & coverage
confirmation): a patient's medical-aid membership, and the availability
slots a practitioner publishes for booking. ``Appointment`` itself
follows in a later migration.

Revision ID: d6f3b8a2c541
Revises: c5e2a9b41f07
Create Date: 2026-09-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d6f3b8a2c541"
down_revision = "c5e2a9b41f07"
branch_labels = None
depends_on = None

_APPOINTMENT_TYPES = (
    "initial_assessment",
    "fitting",
    "review",
    "adjustment",
    "follow_up",
    "other",
)
_SLOT_STATUSES = ("open", "booked", "blocked")


def upgrade() -> None:
    bind = op.get_bind()
    sa.Enum(*_APPOINTMENT_TYPES, name="appointment_type").create(
        bind, checkfirst=True
    )
    sa.Enum(*_SLOT_STATUSES, name="slot_status").create(
        bind, checkfirst=True
    )
    # Column type objects with create_type=False: the types above are
    # already created, and op.create_table's DDL compiler would otherwise
    # try to CREATE TYPE again (regardless of checkfirst on the object
    # used above) and fail with DuplicateObject.
    appointment_type = postgresql.ENUM(
        *_APPOINTMENT_TYPES, name="appointment_type", create_type=False
    )
    slot_status = postgresql.ENUM(
        *_SLOT_STATUSES, name="slot_status", create_type=False
    )

    op.create_table(
        "medical_aid_memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("scheme_name", sa.String(length=200), nullable=False),
        sa.Column("plan_option", sa.String(length=200), nullable=False),
        sa.Column("membership_number", sa.String(length=100), nullable=False),
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
            ["patient_id"], ["patients.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_medical_aid_memberships_patient_id",
        "medical_aid_memberships",
        ["patient_id"],
        unique=True,
    )

    op.create_table(
        "availability_slots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("practice_id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=True),
        sa.Column("practitioner_id", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("end_time", sa.DateTime(), nullable=False),
        sa.Column("appointment_type", appointment_type, nullable=False),
        sa.Column(
            "status",
            slot_status,
            nullable=False,
            server_default="open",
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
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["practitioner_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_availability_slots_practice_id",
        "availability_slots",
        ["practice_id"],
    )
    op.create_index(
        "ix_availability_slots_site_id", "availability_slots", ["site_id"]
    )
    op.create_index(
        "ix_availability_slots_practitioner_id",
        "availability_slots",
        ["practitioner_id"],
    )
    op.create_index(
        "ix_availability_slots_start_time",
        "availability_slots",
        ["start_time"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_availability_slots_start_time", table_name="availability_slots"
    )
    op.drop_index(
        "ix_availability_slots_practitioner_id",
        table_name="availability_slots",
    )
    op.drop_index(
        "ix_availability_slots_site_id", table_name="availability_slots"
    )
    op.drop_index(
        "ix_availability_slots_practice_id", table_name="availability_slots"
    )
    op.drop_table("availability_slots")

    op.drop_index(
        "ix_medical_aid_memberships_patient_id",
        table_name="medical_aid_memberships",
    )
    op.drop_table("medical_aid_memberships")

    bind = op.get_bind()
    sa.Enum(name="slot_status").drop(bind, checkfirst=True)
    sa.Enum(name="appointment_type").drop(bind, checkfirst=True)
