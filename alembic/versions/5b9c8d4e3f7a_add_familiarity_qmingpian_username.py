"""add_familiarity_and_qmingpian_username

Revision ID: 5b9c8d4e3f7a
Revises: 4a8b7c2e1d5f
Create Date: 2026-05-12 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5b9c8d4e3f7a'
down_revision: Union[str, None] = '4a8b7c2e1d5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('investors',
        sa.Column('familiarity', sa.String(length=30), nullable=True))
    op.add_column('ir_users',
        sa.Column('qmingpian_username', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('ir_users', 'qmingpian_username')
    op.drop_column('investors', 'familiarity')
