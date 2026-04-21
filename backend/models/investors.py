from sqlalchemy import Column, Integer, String, Text, JSON, Boolean, DateTime, Date, SmallInteger
from sqlalchemy.sql import func
from database import Base

class Investor(Base):
    __tablename__ = "investors"

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    name                = Column(String(100), nullable=False)
    agency              = Column(String(100))
    position            = Column(String(100))
    email               = Column(JSON)
    wechat              = Column(JSON)
    phone               = Column(JSON)
    industry_tags       = Column(JSON)
    stage_pref          = Column(JSON)
    quota_range         = Column(String(50))
    relationship_score  = Column(SmallInteger, default=0)
    profile_notes       = Column(Text(length=2**32 - 1))
    last_interaction_at = Column(DateTime)
    birthday            = Column(Date)
    join_agency_date    = Column(Date)
    first_meeting_date  = Column(Date)
    is_active           = Column(Boolean, default=True)
    created_at          = Column(DateTime, server_default=func.now())
    updated_at          = Column(DateTime, server_default=func.now(), onupdate=func.now())
