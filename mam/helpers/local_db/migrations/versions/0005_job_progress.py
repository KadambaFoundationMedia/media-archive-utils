"""live job progress

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-22 14:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('job') as batch:
        batch.add_column(sa.Column('step', sa.String(length=200), nullable=True))
        batch.add_column(sa.Column('progress', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('job') as batch:
        batch.drop_column('progress')
        batch.drop_column('step')
