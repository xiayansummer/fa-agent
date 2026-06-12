from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from database import Base


class WxSubQuota(Base):
    """微信订阅消息配额：用户每点一次「允许」（或勾过"总是保持"后静默通过），
    该模板就攒下一条可发送配额；后端发送成功扣 1。
    一次性订阅消息的平台规则：一次授权 = 一条推送，配额不够时提醒发不出去。"""
    __tablename__ = "wx_sub_quota"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    ir_id       = Column(Integer, nullable=False, index=True)
    template_id = Column(String(64), nullable=False)
    times       = Column(Integer, nullable=False, server_default="0")
    updated_at  = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("ir_id", "template_id", name="uq_wx_sub_quota"),
    )
