"""limb_involvements.cause -> causes (JSON list)

Limb loss is often multifactorial. The single ``cause`` enum column
becomes a JSON list ``causes`` of the same values ([] when none). Stored
as JSON, not a PG enum array, so the column also works under SQLite in
the test suite. The ``cause_of_limb_loss`` enum type is kept (still used
by the schema layer and re-created on downgrade).

Revision ID: a5c1e8f4d20b
Revises: c4f7a9e2b610
Create Date: 2026-09-08
"""

import sqlalchemy as sa
from alembic import op

revision = "a5c1e8f4d20b"
down_revision = "c4f7a9e2b610"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "limb_involvements",
        sa.Column(
            "causes",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    # carry the single value across as a one-element list
    op.execute(
        "UPDATE limb_involvements "
        "SET causes = json_build_array(cause::text) "
        "WHERE cause IS NOT NULL"
    )
    op.drop_column("limb_involvements", "cause")


def downgrade() -> None:
    op.add_column(
        "limb_involvements",
        sa.Column(
            "cause",
            sa.Enum(
                "trauma",
                "dysvascular",
                "infection",
                "tumour",
                "congenital",
                "other",
                name="cause_of_limb_loss",
                create_type=False,
            ),
            nullable=True,
        ),
    )
    # keep the first cause; any others are lost
    op.execute(
        "UPDATE limb_involvements "
        "SET cause = (causes ->> 0)::cause_of_limb_loss "
        "WHERE json_array_length(causes) > 0"
    )
    op.drop_column("limb_involvements", "causes")
