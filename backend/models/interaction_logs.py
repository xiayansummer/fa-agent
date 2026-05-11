from sqlalchemy import Column, Integer, SmallInteger, String, Text, Boolean, DateTime, Enum
from sqlalchemy.sql import func
from database import Base

class InteractionLog(Base):
    __tablename__ = "interaction_logs"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    investor_id     = Column(Integer, nullable=False, index=True)
    ir_id           = Column(Integer, nullable=False, index=True)
    type            = Column(Enum("meeting","email","wechat","push","call","other"))
    summary         = Column(Text)
    raw_content     = Column(Text(length=2**32 - 1))
    agent_generated = Column(Boolean, default=False)
    created_at      = Column(DateTime, server_default=func.now())
    occurred_at     = Column(DateTime, nullable=False, server_default=func.now())
    duration_min    = Column(SmallInteger, nullable=True)
    next_followup_at = Column(DateTime, nullable=True)
