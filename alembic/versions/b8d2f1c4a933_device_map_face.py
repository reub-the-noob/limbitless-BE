"""add devices.map_face

Which side of the body-map figure a device's ``map_x`` / ``map_y`` are
measured on: ``anterior`` (default) or ``posterior``. NULL exactly when
the position is NULL.

Revision ID: b8d2f1c4a933
Revises: e7b41c9a2d08
Create Date: 2026-09-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b8d2f1c4a933"
down_revision = "e7b41c9a2d08"
branch_labels = None
depends_on = None

_VALUES = ("anterior", "posterior")


def upgrade() -> None:
    # Pre-create the type, then use a create_type=False handle for the
    # column so add_column doesn't re-issue CREATE TYPE.
    sa.Enum(*_VALUES, name="map_face").create(op.get_bind(), checkfirst=True)
    op.add_column(
        "devices",
        sa.Column(
            "map_face",
            postgresql.ENUM(*_VALUES, name="map_face", create_type=False),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("devices", "map_face")
    sa.Enum(*_VALUES, name="map_face").drop(op.get_bind(), checkfirst=True)
