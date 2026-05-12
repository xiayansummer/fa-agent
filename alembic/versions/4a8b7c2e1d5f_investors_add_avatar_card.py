"""investors_add_avatar_card

Revision ID: 4a8b7c2e1d5f
Revises: 30cc917a9faf
Create Date: 2026-05-12 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '4a8b7c2e1d5f'
down_revision: Union[str, None] = '30cc917a9faf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('investors', sa.Column('avatar_url', sa.String(length=500), nullable=True))
    op.add_column('investors', sa.Column('business_card_url', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('investors', 'business_card_url')
    op.drop_column('investors', 'avatar_url')
