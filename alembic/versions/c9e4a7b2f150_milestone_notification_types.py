"""milestone_due / milestone_overdue notification types

Enum-value additions to ``notification_type`` (requirements Section 5.6:
"a milestone becomes due or overdue"). No table changes. Postgres cannot
drop enum values, so ``downgrade`` is a no-op.

Revision ID: c9e4a7b2f150
Revises: b8d2f5a1c907
Create Date: 2026-09-07
"""

from alembic import op

revision = "c9e4a7b2f150"
down_revision = "b8d2f5a1c907"
branch_labels = None
depends_on = None

_ADDITIONS = ("milestone_due", "milestone_overdue")


def upgrade() -> None:
    for value in _ADDITIONS:
        op.execute(
            f"ALTER TYPE notification_type ADD VALUE IF NOT EXISTS '{value}'"
        )


def downgrade() -> None:
    # Postgres has no "DROP VALUE"; the extra labels are harmless unused.
    pass
