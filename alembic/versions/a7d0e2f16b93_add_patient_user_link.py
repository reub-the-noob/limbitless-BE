"""add patient.user_id (self-service portal link)

A nullable, unique FK from ``patients`` to ``users`` so a ``patient``-role
login can be tied to one clinical record. ``ON DELETE SET NULL`` keeps
the record if the login is removed.

Revision ID: a7d0e2f16b93
Revises: f3a1b2c4d5e6
Create Date: 2026-09-04
"""

import sqlalchemy as sa
from alembic import op

revision = "a7d0e2f16b93"
down_revision = "f3a1b2c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "patients", sa.Column("user_id", sa.Integer(), nullable=True)
    )
    op.create_index(
        "ix_patients_user_id", "patients", ["user_id"], unique=True
    )
    op.create_foreign_key(
        "fk_patients_user_id_users",
        "patients",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_patients_user_id_users", "patients", type_="foreignkey")
    op.drop_index("ix_patients_user_id", table_name="patients")
    op.drop_column("patients", "user_id")
