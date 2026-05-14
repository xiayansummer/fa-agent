from sqlalchemy import Column, Integer, String, Date, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from database import Base


class CalendarDismissal(Base):
    """IR 主动从日历上"删掉"的事件提醒。
    event_key 形如 'meeting:123' / 'action:456' / 'followup:7' / 'birthday:7' / 'anniversary:7:3'。
    event_date 用于 daily 视图按日匹配；对每年复发的里程碑事件，写入时取触发当日。
    """
    __tablename__ = "calendar_dismissals"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    ir_id       = Column(Integer, nullable=False, index=True)
    event_key   = Column(String(128), nullable=False)
    event_date  = Column(Date, nullable=False, index=True)
    created_at  = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("ir_id", "event_key", "event_date", name="uq_dismissal_per_day"),
    )
