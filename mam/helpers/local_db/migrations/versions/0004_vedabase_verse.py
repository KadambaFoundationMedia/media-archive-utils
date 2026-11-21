"""vedabase verse cache

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-21 18:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('vedabase_verse',
    sa.Column('path', sa.String(length=64), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('path')
    )


def downgrade() -> None:
    op.drop_table('vedabase_verse')
