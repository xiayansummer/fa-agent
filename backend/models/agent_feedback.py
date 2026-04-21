from sqlalchemy import Column, Integer, String, Text, Float, JSON, DateTime, Enum
from sqlalchemy.sql import func
from database import Base

class AgentFeedback(Base):
    __tablename__ = "agent_feedback"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    trace_id        = Column(Integer, index=True)
    ir_id           = Column(Integer, index=True)
    investor_ids    = Column(JSON)
    content_type    = Column(Enum("meeting_minutes","industry_report",
                                   "daily_push","milestone_message","investor_list"))
    action          = Column(Enum("approved","modified","rejected"))
    original        = Column(Text(length=2**32 - 1))
    final           = Column(Text(length=2**32 - 1))
    diff_ratio      = Column(Float)
    prompt_version  = Column(String(20))
    response_time_s = Column(Integer)
    created_at      = Column(DateTime, server_default=func.now())
