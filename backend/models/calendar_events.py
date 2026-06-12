from sqlalchemy import Column, Integer, String, Date, DateTime, Text
from sqlalchemy.sql import func
from database import Base


class CalendarEventRow(Base):
    """IR 主动（或 Agent 代为）写入的自由日程。
    与 calendar.py 里现算的 followup/milestone/meeting 不同，这是一等公民、可写可改可删。

    - source='manual'：IR 在日程 Tab 手动新建
    - source='agent' ：Agent 通过 add_calendar_event 工具写入（如「加一条日程，明天下午1点见投资人」）

    按 ir_id 隔离。investor_id 可空（与具体投资人无关的日程，如「下午前滩见投资人」也可不绑）。
    start_time/end_time 存 'HH:MM' 字符串，空表示全天/未指定。
    """
    __tablename__ = "calendar_events"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    ir_id        = Column(Integer, nullable=False, index=True)
    investor_id  = Column(Integer, nullable=True)
    title        = Column(String(200), nullable=False)
    event_date   = Column(Date, nullable=False, index=True)
    start_time   = Column(String(5), nullable=True)   # 'HH:MM'
    end_time     = Column(String(5), nullable=True)   # 'HH:MM'
    location     = Column(String(200), nullable=True)
    notes        = Column(Text, nullable=True)
    source       = Column(String(16), nullable=False, server_default="manual")
    # 提前提醒分钟数：NULL=默认30；-1=不提醒；beat 5分钟粒度，最小有效档 5
    remind_ahead_min = Column(Integer, nullable=True)
    reminded_at  = Column(DateTime, nullable=True)   # 订阅消息提醒已发送时间（去重标记）
    created_at   = Column(DateTime, server_default=func.now())
    updated_at   = Column(DateTime, server_default=func.now(), onupdate=func.now())
