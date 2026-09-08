"""add users.token_version

Session-revocation counter. Every access / refresh token carries the
value current when it was issued; ``POST /auth/logout-all`` bumps it and
auth rejects any token whose ``tv`` no longer matches.

Revision ID: c4f7a9e2b610
Revises: b8d2f1c4a933
Create Date: 2026-09-08
"""

import sqlalchemy as sa
from alembic import op

revision = "c4f7a9e2b610"
down_revision = "b8d2f1c4a933"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
