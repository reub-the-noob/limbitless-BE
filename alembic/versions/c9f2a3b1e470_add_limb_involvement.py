"""add limb_involvement, re-parent devices

Introduces :class:`LimbInvolvement` — one affected body location per
patient (amputation / congenital absence / orthotic need). Devices now
hang off an involvement instead of the patient, and the patient's
``cause_of_limb_loss`` / ``limb_loss_level`` move onto the involvement.
``prosthetic_devices`` loses ``limb_side`` / ``limb_level`` (inherited)
and the "one active device per limb" partial unique index (a patient may
have several active devices for one limb).

This is a schema-only migration: there is no production data, so run
``python -m scripts.seed --reset`` after upgrading to repopulate the dev
database in the new shape.

Revision ID: c9f2a3b1e470
Revises: b7c4e1a90d52
Create Date: 2026-09-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c9f2a3b1e470"
down_revision = "b7c4e1a90d52"
branch_labels = None
depends_on = None

_LEVELS = (
    "partial_foot",
    "ankle_disarticulation",
    "transtibial",
    "knee_disarticulation",
    "transfemoral",
    "hip_disarticulation",
    "partial_hand",
    "wrist_disarticulation",
    "transradial",
    "elbow_disarticulation",
    "transhumeral",
    "shoulder_disarticulation",
)
_CAUSES = ("trauma", "dysvascular", "infection", "tumour", "congenital", "other")


def upgrade() -> None:
    involvement_kind = sa.Enum(
        "amputation",
        "congenital_absence",
        "orthotic_need",
        name="involvement_kind",
    )
    body_region = sa.Enum(
        "lower_limb_left",
        "lower_limb_right",
        "upper_limb_left",
        "upper_limb_right",
        "spine",
        "trunk",
        "other",
        name="body_region",
    )
    involvement_status = sa.Enum("active", "resolved", name="involvement_status")

    op.create_table(
        "limb_involvements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "patient_id",
            sa.Integer(),
            sa.ForeignKey("patients.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", involvement_kind, nullable=False),
        sa.Column("region", body_region, nullable=False),
        sa.Column(
            "level",
            postgresql.ENUM(*_LEVELS, name="limb_loss_level", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "cause",
            postgresql.ENUM(
                *_CAUSES, name="cause_of_limb_loss", create_type=False
            ),
            nullable=True,
        ),
        sa.Column("onset_date", sa.Date(), nullable=True),
        sa.Column(
            "status",
            involvement_status,
            nullable=False,
            server_default="active",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # patients lose the limb-loss summary fields
    op.drop_column("patients", "cause_of_limb_loss")
    op.drop_column("patients", "limb_loss_level")

    # devices re-parent onto an involvement. There is no production data
    # and no automatic backfill (see the module docstring), so clear the
    # table before adding the NOT NULL FK; ``scripts.seed --reset``
    # repopulates it.
    op.execute("DELETE FROM prosthetic_devices")
    op.drop_index("uq_prosthetic_devices_active_limb", table_name="prosthetic_devices")
    op.add_column(
        "prosthetic_devices",
        sa.Column(
            "involvement_id",
            sa.Integer(),
            sa.ForeignKey("limb_involvements.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_prosthetic_devices_involvement_id",
        "prosthetic_devices",
        ["involvement_id"],
    )
    op.add_column(
        "prosthetic_devices",
        sa.Column("mount_location", sa.String(length=200), nullable=True),
    )
    op.drop_column("prosthetic_devices", "patient_id")
    op.drop_column("prosthetic_devices", "limb_side")
    op.drop_column("prosthetic_devices", "limb_level")


def downgrade() -> None:
    op.add_column(
        "prosthetic_devices",
        sa.Column(
            "limb_level",
            postgresql.ENUM(*_LEVELS, name="limb_loss_level", create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "prosthetic_devices",
        sa.Column(
            "limb_side",
            postgresql.ENUM("left", "right", "bilateral", name="limb_side"),
            nullable=True,
        ),
    )
    op.add_column(
        "prosthetic_devices",
        sa.Column(
            "patient_id",
            sa.Integer(),
            sa.ForeignKey("patients.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_prosthetic_devices_patient_id",
        "prosthetic_devices",
        ["patient_id"],
    )
    op.create_index(
        "uq_prosthetic_devices_active_limb",
        "prosthetic_devices",
        ["patient_id", "limb_side"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    op.drop_column("prosthetic_devices", "mount_location")
    op.drop_index(
        "ix_prosthetic_devices_involvement_id", table_name="prosthetic_devices"
    )
    op.drop_column("prosthetic_devices", "involvement_id")

    op.add_column(
        "patients",
        sa.Column(
            "limb_loss_level",
            postgresql.ENUM(*_LEVELS, name="limb_loss_level", create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "patients",
        sa.Column(
            "cause_of_limb_loss",
            postgresql.ENUM(
                *_CAUSES, name="cause_of_limb_loss", create_type=False
            ),
            nullable=True,
        ),
    )

    op.drop_table("limb_involvements")
    sa.Enum(name="involvement_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="body_region").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="involvement_kind").drop(op.get_bind(), checkfirst=True)
