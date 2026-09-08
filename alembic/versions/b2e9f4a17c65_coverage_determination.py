"""add coverage_determinations and user scheme_name

Section 5.9's coverage confirmation step, captured manually by a
medical-aid reviewer - never fetched live from a scheme's own systems.
Also the reconciliation half: ``users.scheme_name`` (meaningful only for
a medical_aid_reviewer) is what gives a reviewer automatic, cross-practice
access to any patient whose active MedicalAidMembership matches - the
requirements doc's real design for Section 5.12, alongside (not instead
of) the manual per-patient ReviewGrant from R-34.

Revision ID: b2e9f4a17c65
Revises: f8a5c1e7d234
Create Date: 2026-09-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b2e9f4a17c65"
down_revision = "f8a5c1e7d234"
branch_labels = None
depends_on = None

_COVERAGE_STATUSES = ("pending", "approved", "denied")


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("scheme_name", sa.String(length=200), nullable=True)
    )

    bind = op.get_bind()
    sa.Enum(*_COVERAGE_STATUSES, name="coverage_status").create(
        bind, checkfirst=True
    )
    # create_type=False: the type is already created above - op.create_table's
    # DDL compiler would otherwise re-issue CREATE TYPE and fail with
    # DuplicateObject (see d6f3b8a2c541 for the same gotcha).
    coverage_status = postgresql.ENUM(
        *_COVERAGE_STATUSES, name="coverage_status", create_type=False
    )

    op.create_table(
        "coverage_determinations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("appointment_id", sa.Integer(), nullable=False),
        sa.Column("medical_aid_membership_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            coverage_status,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("authorization_number", sa.String(length=100), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("decided_by_id", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
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
            ["appointment_id"], ["appointments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["medical_aid_membership_id"],
            ["medical_aid_memberships.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_coverage_determinations_appointment_id",
        "coverage_determinations",
        ["appointment_id"],
        unique=True,
    )
    op.create_index(
        "ix_coverage_determinations_medical_aid_membership_id",
        "coverage_determinations",
        ["medical_aid_membership_id"],
    )
    op.create_index(
        "ix_coverage_determinations_decided_by_id",
        "coverage_determinations",
        ["decided_by_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_coverage_determinations_decided_by_id",
        table_name="coverage_determinations",
    )
    op.drop_index(
        "ix_coverage_determinations_medical_aid_membership_id",
        table_name="coverage_determinations",
    )
    op.drop_index(
        "ix_coverage_determinations_appointment_id",
        table_name="coverage_determinations",
    )
    op.drop_table("coverage_determinations")

    bind = op.get_bind()
    sa.Enum(name="coverage_status").drop(bind, checkfirst=True)

    op.drop_column("users", "scheme_name")
