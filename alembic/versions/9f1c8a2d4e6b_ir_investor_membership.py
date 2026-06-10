"""ir_investor_membership: 投资人按 IR 隔离的归属表 + backfill

Revision ID: 9f1c8a2d4e6b
Revises: 5b9c8d4e3f7a
Create Date: 2026-06-05 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9f1c8a2d4e6b'
down_revision: Union[str, None] = '5b9c8d4e3f7a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 建表
    op.create_table(
        "ir_investor_membership",
        sa.Column("ir_id", sa.Integer(), nullable=False),
        sa.Column("investor_id", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["ir_id"], ["ir_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["investor_id"], ["investors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ir_id", "investor_id"),
    )

    # 2) backfill：从现有 InteractionLog + OutreachRecord 推导关系，
    #    保证迁移完成后已存在的 IR-投资人 关系都被搬到归属表里。
    #    用 INSERT IGNORE 防止重复键冲突（MySQL 方言）。
    op.execute("""
        INSERT IGNORE INTO ir_investor_membership (ir_id, investor_id, added_at)
        SELECT ir_id, investor_id, MIN(COALESCE(occurred_at, created_at))
        FROM interaction_logs
        WHERE ir_id IS NOT NULL AND investor_id IS NOT NULL
        GROUP BY ir_id, investor_id
    """)
    op.execute("""
        INSERT IGNORE INTO ir_investor_membership (ir_id, investor_id, added_at)
        SELECT ir_id, investor_id, MIN(created_at)
        FROM outreach_records
        WHERE ir_id IS NOT NULL AND investor_id IS NOT NULL
        GROUP BY ir_id, investor_id
    """)


def downgrade() -> None:
    op.drop_table("ir_investor_membership")
