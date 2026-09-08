"""orthotic PROMs and pathway

Add the enum values behind the orthotic care pathway and its
outcome measures:
- ``care_pathway`` gains ``orthotic``
- ``milestone_type`` gains ``orthotic_assessment`` / ``orthosis_casting``
- ``prom_instrument`` gains ``orthosis_comfort_score`` /
  ``quest_satisfaction``

Enum-value additions only; no table changes. Postgres cannot drop enum
values, so ``downgrade`` is a no-op (the values are harmless if unused).

Revision ID: e2f9c7d34a10
Revises: d1e8b4a06c33
Create Date: 2026-09-04
"""

from alembic import op

revision = "e2f9c7d34a10"
down_revision = "d1e8b4a06c33"
branch_labels = None
depends_on = None

_ADDITIONS = {
    "care_pathway": ("orthotic",),
    "milestone_type": ("orthotic_assessment", "orthosis_casting"),
    "prom_instrument": ("orthosis_comfort_score", "quest_satisfaction"),
}


def upgrade() -> None:
    for enum_name, values in _ADDITIONS.items():
        for value in values:
            op.execute(
                f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{value}'"
            )


def downgrade() -> None:
    # Postgres has no "DROP VALUE"; leaving the extra labels in place is
    # safe as long as no rows use them.
    pass
