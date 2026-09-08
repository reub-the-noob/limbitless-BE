"""optional involvement_id on milestone / prom / note

A recovery milestone, PROM measure or clinical note can now be tied to a
specific :class:`LimbInvolvement` (a limb), not just the patient. The
column is nullable — patient-level rows stay valid — and ON DELETE SET
NULL so retiring an involvement leaves the history intact.

Revision ID: d1e8b4a06c33
Revises: c9f2a3b1e470
Create Date: 2026-09-04
"""

import sqlalchemy as sa
from alembic import op

revision = "d1e8b4a06c33"
down_revision = "c9f2a3b1e470"
branch_labels = None
depends_on = None

_TABLES = ("recovery_milestones", "prom_records", "clinical_notes")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "involvement_id",
                sa.Integer(),
                sa.ForeignKey("limb_involvements.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.create_index(
            f"ix_{table}_involvement_id", table, ["involvement_id"]
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_index(f"ix_{table}_involvement_id", table_name=table)
        op.drop_column(table, "involvement_id")
