"""add account_link_requests

The patient self-service walk-in-record claim flow (requirements
Section 5.11): a patient-role login claiming an existing walk-in
Patient record by identity number, gated by a second factor (a contact
email or phone already on the record).

Revision ID: d3f8a6c2b917
Revises: b2e9f4a17c65
Create Date: 2026-09-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d3f8a6c2b917"
down_revision = "b2e9f4a17c65"
branch_labels = None
depends_on = None

_VERIFICATION_METHODS = ("contact_email", "contact_phone")
_ACCOUNT_LINK_STATUSES = ("pending", "verified", "rejected")


def upgrade() -> None:
    bind = op.get_bind()
    sa.Enum(*_VERIFICATION_METHODS, name="verification_method").create(
        bind, checkfirst=True
    )
    sa.Enum(*_ACCOUNT_LINK_STATUSES, name="account_link_status").create(
        bind, checkfirst=True
    )
    # create_type=False: the types are already created above -
    # op.create_table's DDL compiler would otherwise re-issue CREATE TYPE
    # and fail with DuplicateObject (see d6f3b8a2c541 for the same gotcha).
    verification_method = postgresql.ENUM(
        *_VERIFICATION_METHODS, name="verification_method", create_type=False
    )
    account_link_status = postgresql.ENUM(
        *_ACCOUNT_LINK_STATUSES, name="account_link_status", create_type=False
    )

    op.create_table(
        "account_link_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=True),
        sa.Column(
            "verification_method", verification_method, nullable=False
        ),
        sa.Column(
            "status",
            account_link_status,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
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
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patients.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_account_link_requests_user_id",
        "account_link_requests",
        ["user_id"],
    )
    op.create_index(
        "ix_account_link_requests_patient_id",
        "account_link_requests",
        ["patient_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_account_link_requests_patient_id",
        table_name="account_link_requests",
    )
    op.drop_index(
        "ix_account_link_requests_user_id",
        table_name="account_link_requests",
    )
    op.drop_table("account_link_requests")

    bind = op.get_bind()
    sa.Enum(name="account_link_status").drop(bind, checkfirst=True)
    sa.Enum(name="verification_method").drop(bind, checkfirst=True)
