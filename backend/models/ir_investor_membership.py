"""IR ↔ Investor 多对多归属表。

投资人表是共享池（无 owner 列），归属用本表显式建立——同一投资人可被多 IR 加入
各自的库。`_my_investor_ids` 用本表 + 旧 InteractionLog/OutreachRecord 推导兜底。
"""
from sqlalchemy import Column, Integer, DateTime, ForeignKey, PrimaryKeyConstraint
from sqlalchemy.sql import func
from database import Base


class IrInvestorMembership(Base):
    __tablename__ = "ir_investor_membership"

    ir_id       = Column(Integer, ForeignKey("ir_users.id", ondelete="CASCADE"), nullable=False)
    investor_id = Column(Integer, ForeignKey("investors.id", ondelete="CASCADE"), nullable=False)
    added_at    = Column(DateTime, server_default=func.now())

    __table_args__ = (PrimaryKeyConstraint("ir_id", "investor_id"),)
