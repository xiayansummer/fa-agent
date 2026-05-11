import calendar as _calendar
from fastapi import APIRouter, Depends, Query, HTTPException
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

class MonthCalendarOut(BaseModel):
    month: str          # "YYYY-MM"
    days: dict[str, list[str]]  # date_str → list of unique event types


def _compute_events_for_day(investors, target_date: date) -> list[CalendarEvent]:
    """Return all CalendarEvent objects for a given date across all investors."""
    events: list[CalendarEvent] = []

    for inv in investors:
        # Followup: last interaction >= 14 days ago
        if inv.last_interaction_at:
            days_since = (target_date - inv.last_interaction_at.date()).days
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
        if inv.birthday and inv.birthday.month == target_date.month and inv.birthday.day == target_date.day:
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
            years = target_date.year - inv.join_agency_date.year
            if (years > 0
                    and inv.join_agency_date.month == target_date.month
                    and inv.join_agency_date.day == target_date.day):
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
    return events


@router.get("/daily", response_model=DailyCalendarOut)
async def get_daily_calendar(
    target_date: Optional[str] = Query(None, description="YYYY-MM-DD, defaults to today"),
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    ir_id = current_ir["ir_id"]
    cal_date = date.fromisoformat(target_date) if target_date else date.today()

    result = await db.execute(select(Investor).where(Investor.is_active == True))
    investors = result.scalars().all()

    events = _compute_events_for_day(investors, cal_date)
    return DailyCalendarOut(date=str(cal_date), ir_id=ir_id, events=events)


@router.get("/month", response_model=MonthCalendarOut)
async def get_month_calendar(
    month: str = Query(..., description="YYYY-MM"),
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    # Validate and parse month
    try:
        first_day = date.fromisoformat(f"{month}-01")
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid month format: {month!r}. Expected YYYY-MM.")

    year = first_day.year
    month_num = first_day.month
    _, last_day_num = _calendar.monthrange(year, month_num)

    result = await db.execute(select(Investor).where(Investor.is_active == True))
    investors = result.scalars().all()

    days: dict[str, list[str]] = {}
    for day_num in range(1, last_day_num + 1):
        target_date = date(year, month_num, day_num)
        events = _compute_events_for_day(investors, target_date)
        if events:
            # Collect unique event types, preserving first-occurrence order
            seen: dict[str, None] = {}
            for e in events:
                seen[e.type] = None
            days[str(target_date)] = list(seen.keys())

    return MonthCalendarOut(month=month, days=days)
