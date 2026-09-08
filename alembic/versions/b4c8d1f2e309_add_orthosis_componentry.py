"""add orthosis componentry columns to devices

Four nullable string columns mirroring the prosthesis componentry set
(``socket_type`` etc.). The frontend shows one set or the other by device
type; the database keeps both, all optional.

Revision ID: b4c8d1f2e309
Revises: a7d0e2f16b93
Create Date: 2026-09-04
"""

import sqlalchemy as sa
from alembic import op

revision = "b4c8d1f2e309"
down_revision = "a7d0e2f16b93"
branch_labels = None
depends_on = None

_COLUMNS = ("joint_type", "trimline", "strap_configuration", "padding_liner")


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column(
            "devices", sa.Column(name, sa.String(length=200), nullable=True)
        )


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("devices", name)
