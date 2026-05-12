from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, LargeBinary
from sqlalchemy.sql import func
from database import Base

class IRUser(Base):
    __tablename__ = "ir_users"

    id                              = Column(Integer, primary_key=True, autoincrement=True)
    name                            = Column(String(50), nullable=False)
    phone                           = Column(String(20), unique=True, nullable=True)
    wechat_openid                   = Column(String(64), unique=True, nullable=True)
    role                            = Column(Enum("ir", "admin"), default="ir")
    is_active                       = Column(Boolean, default=True)
    created_at                      = Column(DateTime, server_default=func.now())
    tencent_meeting_token_encrypted = Column(LargeBinary(512), nullable=True)
    qmingpian_username              = Column(String(100), nullable=True)  # PC 端企名片登录用户名（如 'Investarget'）
