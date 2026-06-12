"""wx 订阅消息日程提醒：配额表 + calendar_events.reminded_at

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-12 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wx_sub_quota",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ir_id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.String(length=64), nullable=False),
        sa.Column("times", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ir_id", "template_id", name="uq_wx_sub_quota"),
    )
    op.create_index("ix_wx_sub_quota_ir_id", "wx_sub_quota", ["ir_id"])
    op.add_column("calendar_events", sa.Column("reminded_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("calendar_events", "reminded_at")
    op.drop_index("ix_wx_sub_quota_ir_id", table_name="wx_sub_quota")
    op.drop_table("wx_sub_quota")
