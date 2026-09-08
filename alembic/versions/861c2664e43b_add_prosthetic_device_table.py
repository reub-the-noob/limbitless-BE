"""add prosthetic_device table

Revision ID: 861c2664e43b
Revises: 1f3fdda5a25e
Create Date: 2026-09-03 10:12:42.757972

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '861c2664e43b'
down_revision: Union[str, Sequence[str], None] = '1f3fdda5a25e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LIMB_LEVELS = (
    'partial_foot', 'ankle_disarticulation', 'transtibial',
    'knee_disarticulation', 'transfemoral', 'hip_disarticulation',
    'partial_hand', 'wrist_disarticulation', 'transradial',
    'elbow_disarticulation', 'transhumeral', 'shoulder_disarticulation',
)
DEVICE_TYPE = ('body_powered', 'myoelectric', 'passive_cosmetic', 'activity_specific')
DEVICE_STATUS = ('planned', 'in_fitting', 'active', 'in_repair', 'replaced', 'retired')
LIMB_SIDE = ('left', 'right')


def upgrade() -> None:
    """Upgrade schema."""
    # op.create_table creates the enum types it introduces (limb_side,
    # device_type, device_status). limb_loss_level already exists from the
    # patient table, so it is referenced with create_type=False via the
    # PostgreSQL ENUM type, which honours that flag inside create_table.
    op.create_table(
        'prosthetic_devices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('patient_id', sa.Integer(), nullable=False),
        sa.Column(
            'limb_side', sa.Enum(*LIMB_SIDE, name='limb_side'), nullable=False
        ),
        sa.Column(
            'limb_level',
            postgresql.ENUM(*LIMB_LEVELS, name='limb_loss_level', create_type=False),
            nullable=False,
        ),
        sa.Column(
            'device_type',
            sa.Enum(*DEVICE_TYPE, name='device_type'),
            nullable=False,
        ),
        sa.Column(
            'status',
            sa.Enum(*DEVICE_STATUS, name='device_status'),
            nullable=False,
        ),
        sa.Column('manufacturer', sa.String(length=200), nullable=True),
        sa.Column('model', sa.String(length=200), nullable=True),
        sa.Column('serial_number', sa.String(length=120), nullable=True),
        sa.Column('socket_type', sa.String(length=200), nullable=True),
        sa.Column('liner_type', sa.String(length=200), nullable=True),
        sa.Column('suspension_type', sa.String(length=200), nullable=True),
        sa.Column('terminal_device', sa.String(length=200), nullable=True),
        sa.Column('cast_scan_date', sa.Date(), nullable=True),
        sa.Column('delivery_date', sa.Date(), nullable=True),
        sa.Column('fitted_date', sa.Date(), nullable=True),
        sa.Column('warranty_start', sa.Date(), nullable=True),
        sa.Column('warranty_expiry', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('replaces_device_id', sa.Integer(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False
        ),
        sa.Column(
            'updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False
        ),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(
            ['replaces_device_id'], ['prosthetic_devices.id'], ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_prosthetic_devices_patient_id'),
        'prosthetic_devices',
        ['patient_id'],
        unique=False,
    )
    op.create_index(
        'uq_prosthetic_devices_active_limb',
        'prosthetic_devices',
        ['patient_id', 'limb_side'],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'uq_prosthetic_devices_active_limb',
        table_name='prosthetic_devices',
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    op.drop_index(
        op.f('ix_prosthetic_devices_patient_id'), table_name='prosthetic_devices'
    )
    op.drop_table('prosthetic_devices')

    bind = op.get_bind()
    # limb_loss_level is left alone - it belongs to the patient table.
    sa.Enum(name='device_status').drop(bind, checkfirst=True)
    sa.Enum(name='device_type').drop(bind, checkfirst=True)
    sa.Enum(name='limb_side').drop(bind, checkfirst=True)
