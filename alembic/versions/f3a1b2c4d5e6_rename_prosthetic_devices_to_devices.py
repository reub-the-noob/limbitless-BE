"""rename prosthetic_devices to devices

The table has held orthoses as well as prostheses since R-23; the model
class became ``Device`` in R-30. Rename the table and its owned objects
(sequence, primary-key and involvement indexes, the two foreign-key
constraints) to match. Data and columns are untouched; foreign keys from
``recovery_milestones`` / ``prom_records`` follow the table automatically.

Revision ID: f3a1b2c4d5e6
Revises: e2f9c7d34a10
Create Date: 2026-09-04
"""

from alembic import op

revision = "f3a1b2c4d5e6"
down_revision = "e2f9c7d34a10"
branch_labels = None
depends_on = None

# (old name, new name)
_RENAMES = [
    ("SEQUENCE prosthetic_devices_id_seq", "devices_id_seq"),
    ("INDEX prosthetic_devices_pkey", "devices_pkey"),
    (
        "INDEX ix_prosthetic_devices_involvement_id",
        "ix_devices_involvement_id",
    ),
]
_CONSTRAINTS = [
    (
        "prosthetic_devices_involvement_id_fkey",
        "devices_involvement_id_fkey",
    ),
    (
        "prosthetic_devices_replaces_device_id_fkey",
        "devices_replaces_device_id_fkey",
    ),
]


def upgrade() -> None:
    op.rename_table("prosthetic_devices", "devices")
    for old, new in _RENAMES:
        op.execute(f"ALTER {old} RENAME TO {new}")
    for old, new in _CONSTRAINTS:
        op.execute(f"ALTER TABLE devices RENAME CONSTRAINT {old} TO {new}")


def downgrade() -> None:
    for old, new in _CONSTRAINTS:
        op.execute(f"ALTER TABLE devices RENAME CONSTRAINT {new} TO {old}")
    for old, new in reversed(_RENAMES):
        kind = old.split(" ", 1)[0]
        old_name = old.split(" ", 1)[1]
        op.execute(f"ALTER {kind} {new} RENAME TO {old_name}")
    op.rename_table("devices", "prosthetic_devices")
