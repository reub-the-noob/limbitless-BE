"""move the portal link to a join table (multi-practice walk-in claim)

``Patient.user_id`` was 1:1 (one record, one login). §5.11 wants a
person seen as a walk-in at more than one practice to hold all of their
records on one login, so the link moves to ``account_patient_links``
(``patient_id`` unique, ``user_id`` not).

Revision ID: e7b41c9a2d08
Revises: d1a6c3f80b45
Create Date: 2026-09-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e7b41c9a2d08"
down_revision = "d1a6c3f80b45"
branch_labels = None
depends_on = None

# The enum type already exists (created by d3f8a6c2b917); reuse it
# without re-issuing CREATE TYPE.
_verification_method = postgresql.ENUM(
    "contact_email", "contact_phone", name="verification_method", create_type=False
)


def upgrade() -> None:
    op.create_table(
        "account_patient_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("verification_method", _verification_method, nullable=True),
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
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_account_patient_links_patient_id",
        "account_patient_links",
        ["patient_id"],
        unique=True,
    )
    op.create_index(
        "ix_account_patient_links_user_id", "account_patient_links", ["user_id"]
    )

    # carry the existing 1:1 links over
    op.execute(
        """
        INSERT INTO account_patient_links (patient_id, user_id, created_at, updated_at)
        SELECT id, user_id, now(), now() FROM patients WHERE user_id IS NOT NULL
        """
    )

    op.drop_constraint("fk_patients_user_id_users", "patients", type_="foreignkey")
    op.drop_index("ix_patients_user_id", table_name="patients")
    op.drop_column("patients", "user_id")


def downgrade() -> None:
    op.add_column(
        "patients", sa.Column("user_id", sa.Integer(), nullable=True)
    )
    op.create_index("ix_patients_user_id", "patients", ["user_id"], unique=True)
    op.create_foreign_key(
        "fk_patients_user_id_users",
        "patients",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE patients p SET user_id = l.user_id
        FROM account_patient_links l WHERE l.patient_id = p.id
        """
    )
    op.drop_index(
        "ix_account_patient_links_user_id", table_name="account_patient_links"
    )
    op.drop_index(
        "ix_account_patient_links_patient_id", table_name="account_patient_links"
    )
    op.drop_table("account_patient_links")
