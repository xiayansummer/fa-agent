"""calendar_events.remind_ahead_min: 每条日程可设提前提醒分钟（NULL=默认30，-1=不提醒）

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-12 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("calendar_events", sa.Column("remind_ahead_min", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("calendar_events", "remind_ahead_min")
