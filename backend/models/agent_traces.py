from sqlalchemy import Column, Integer, String, JSON, DateTime, Enum
from sqlalchemy.sql import func
from database import Base

class AgentTrace(Base):
    __tablename__ = "agent_traces"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    thread_id       = Column(String(64), index=True)
    ir_id           = Column(Integer, index=True)
    agent_name      = Column(String(50))
    prompt_version  = Column(String(20))
    input_tokens    = Column(Integer)
    output_tokens   = Column(Integer)
    latency_ms      = Column(Integer)
    skills_called   = Column(JSON)
    status          = Column(Enum("success","retry","failed"))
    error_message   = Column(String(500))
    created_at      = Column(DateTime, server_default=func.now())
