"""orthoses and bilateral devices

Add orthotic values to ``device_type``, a ``bilateral`` value to
``limb_side``, and make ``prosthetic_devices.limb_level`` nullable so a
device can be an orthosis (which has no amputation level).

Revision ID: b7c4e1a90d52
Revises: 250214a29b3a
Create Date: 2026-09-04
"""

from alembic import op

revision = "b7c4e1a90d52"
down_revision = "250214a29b3a"
branch_labels = None
depends_on = None

_NEW_DEVICE_TYPES = (
    "orthosis_afo",
    "orthosis_kafo",
    "orthosis_spinal",
    "orthosis_upper_limb",
)


def upgrade() -> None:
    for value in _NEW_DEVICE_TYPES:
        op.execute(f"ALTER TYPE device_type ADD VALUE IF NOT EXISTS '{value}'")
    op.execute("ALTER TYPE limb_side ADD VALUE IF NOT EXISTS 'bilateral'")
    op.alter_column("prosthetic_devices", "limb_level", nullable=True)


def downgrade() -> None:
    # Postgres cannot drop enum values; only the column nullability is
    # reverted. This will fail if any orthosis rows (limb_level IS NULL)
    # exist - delete them first if you need to downgrade.
    op.alter_column("prosthetic_devices", "limb_level", nullable=False)
