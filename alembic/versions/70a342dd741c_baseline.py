"""baseline

Revision ID: 70a342dd741c
Revises: 
Create Date: 2026-09-02 20:39:38.507889

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '70a342dd741c'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Empty root revision.

    Establishes the migration chain so every real schema change (from
    R-04 onward) has a parent to revise. No tables exist yet.
    """


def downgrade() -> None:
    """No-op: nothing to undo for the baseline."""
