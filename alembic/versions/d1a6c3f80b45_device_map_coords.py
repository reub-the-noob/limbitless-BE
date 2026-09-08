"""add devices.map_x / map_y

Optional precise body-map position for a device (requirements Section
5.8): viewBox fractions in [0, 1], set together or not at all. A device
without them falls back to its involvement's region marker.

Revision ID: d1a6c3f80b45
Revises: c9e4a7b2f150
Create Date: 2026-09-07
"""

import sqlalchemy as sa
from alembic import op

revision = "d1a6c3f80b45"
down_revision = "c9e4a7b2f150"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("map_x", sa.Float(), nullable=True))
    op.add_column("devices", sa.Column("map_y", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "map_y")
    op.drop_column("devices", "map_x")
