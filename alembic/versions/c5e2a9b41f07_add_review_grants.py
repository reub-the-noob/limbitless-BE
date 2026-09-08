"""add review_grants (medical-aid reviewer access)

A join row per (patient, reviewer): a treating-practice staff member
grants a medical-aid reviewer read-only access to one patient's record.

Revision ID: c5e2a9b41f07
Revises: b4c8d1f2e309
Create Date: 2026-09-04
"""

import sqlalchemy as sa
from alembic import op

revision = "c5e2a9b41f07"
down_revision = "b4c8d1f2e309"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("granted_by_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["reviewer_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "patient_id",
            "reviewer_id",
            name="uq_review_grants_patient_reviewer",
        ),
    )
    op.create_index(
        "ix_review_grants_patient_id", "review_grants", ["patient_id"]
    )
    op.create_index(
        "ix_review_grants_reviewer_id", "review_grants", ["reviewer_id"]
    )
    op.create_index(
        "ix_review_grants_granted_by_id", "review_grants", ["granted_by_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_review_grants_granted_by_id", table_name="review_grants")
    op.drop_index("ix_review_grants_reviewer_id", table_name="review_grants")
    op.drop_index("ix_review_grants_patient_id", table_name="review_grants")
    op.drop_table("review_grants")
