from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel
from database import get_db
from models.investors import Investor
from auth.jwt import get_current_ir

router = APIRouter()

class CalendarEvent(BaseModel):
    time: str
    type: str
    title: str
    description: str
    investor_id: int
    investor_name: str
    action_label: str
    action_prefill: str

class DailyCalendarOut(BaseModel):
    date: str
    ir_id: int
    events: list[CalendarEvent]

@router.get("/daily", response_model=DailyCalendarOut)
async def get_daily_calendar(
    target_date: Optional[str] = Query(None, description="YYYY-MM-DD, defaults to today"),
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    ir_id = current_ir["ir_id"]
    cal_date = date.fromisoformat(target_date) if target_date else date.today()
    events: list[CalendarEvent] = []

    result = await db.execute(select(Investor).where(Investor.is_active == True))
    investors = result.scalars().all()

    for inv in investors:
        # Followup: last interaction > 14 days ago
        if inv.last_interaction_at:
            days_since = (cal_date - inv.last_interaction_at.date()).days
            if days_since >= 14:
                events.append(CalendarEvent(
                    time="09:00",
                    type="followup",
                    title=f"跟进{inv.name}（{inv.agency or ''}）",
                    description=f"上次互动 {days_since} 天前",
                    investor_id=inv.id,
                    investor_name=inv.name,
                    action_label="执行",
                    action_prefill=f"帮我跟进{inv.name}，生成一条行业推送",
                ))

        # Birthday milestone
        if inv.birthday and inv.birthday.month == cal_date.month and inv.birthday.day == cal_date.day:
            events.append(CalendarEvent(
                time="10:00",
                type="milestone",
                title=f"{inv.name} 生日",
                description=f"{inv.agency or ''} · {inv.position or ''}",
                investor_id=inv.id,
                investor_name=inv.name,
                action_label="审核祝贺",
                action_prefill=f"为{inv.name}生成生日祝贺消息",
            ))

        # Join agency anniversary
        if inv.join_agency_date:
            years = cal_date.year - inv.join_agency_date.year
            if (years > 0
                    and inv.join_agency_date.month == cal_date.month
                    and inv.join_agency_date.day == cal_date.day):
                events.append(CalendarEvent(
                    time="10:30",
                    type="milestone",
                    title=f"{inv.name} 加入{inv.agency or ''} {years} 周年",
                    description="里程碑节点，建议发送祝贺",
                    investor_id=inv.id,
                    investor_name=inv.name,
                    action_label="审核祝贺",
                    action_prefill=f"为{inv.name}生成加入{inv.agency or ''}{years}周年祝贺消息",
                ))

    events.sort(key=lambda e: e.time)
    return DailyCalendarOut(date=str(cal_date), ir_id=ir_id, events=events)
