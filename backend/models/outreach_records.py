from sqlalchemy import Column, Integer, String, Text, DateTime, Enum
from sqlalchemy.sql import func
from database import Base

class OutreachRecord(Base):
    __tablename__ = "outreach_records"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    investor_id = Column(Integer, nullable=False, index=True)
    ir_id       = Column(Integer, nullable=False, index=True)
    type        = Column(Enum("meeting_minutes","industry_report","daily_push","milestone_message"))
    channel     = Column(Enum("wechat","email","qmingpian"), default="wechat")
    content     = Column(Text(length=2**32 - 1))
    status      = Column(Enum("draft","approved","sent","failed"), default="draft")
    sent_at     = Column(DateTime)
    created_at  = Column(DateTime, server_default=func.now())
