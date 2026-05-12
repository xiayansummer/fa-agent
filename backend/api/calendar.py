import asyncio
import calendar as _calendar
import json
import logging
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import date, datetime, timedelta
from typing import Optional
from pydantic import BaseModel
from database import get_db
from models.investors import Investor
from models.ir_users import IRUser
from auth.jwt import get_current_ir
from redis_client import get_redis
from services import crypto_service
from services.tencent_meeting import TencentMeetingClient, TencentAuthError, TencentToolError

router = APIRouter()
logger = logging.getLogger(__name__)

class CalendarEvent(BaseModel):
    time: str
    type: str
    title: str
    description: str
    investor_id: int = 0  # 0 表示与具体投资人无关（如会议）
    investor_name: str = ""
    action_label: str
    action_prefill: str
    tencent_meeting_id: Optional[str] = None  # 仅 type=meeting 时有值

class DailyCalendarOut(BaseModel):
    date: str
    ir_id: int
    events: list[CalendarEvent]

class MonthCalendarOut(BaseModel):
    month: str          # "YYYY-MM"
    days: dict[str, list[str]]  # date_str → list of unique event types


async def _load_tencent_meetings(db: AsyncSession, ir_id: int) -> list[dict]:
    """拉当前 IR 的腾讯会议（最近 60 天 ended + upcoming），按日期归一化。
    返回每条 {date: 'YYYY-MM-DD', time: 'HH:MM', subject, meeting_id, end_time}。
    失败静默返回 []（不阻塞日历）。
    使用 Redis 5min 缓存避免每次月历请求都打腾讯 API。
    """
    # Redis 缓存
    try:
        redis = await get_redis()
        cache_key = f"tencent:meetings:ir:{ir_id}"
        cached = await redis.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        redis = None  # Redis 不可用，降级到直接调

    # 取 token
    result = await db.execute(select(IRUser).where(IRUser.id == ir_id))
    user = result.scalar_one_or_none()
    if not user or not user.tencent_meeting_token_encrypted:
        return []
    try:
        token = crypto_service.decrypt(user.tencent_meeting_token_encrypted)
    except Exception:
        return []

    client = TencentMeetingClient(token=token)
    items: list[dict] = []
    now = datetime.now()
    # 5s 硬超时保护：腾讯偶发慢时降级返回空，不拖垮日历端点
    try:
        ended = await asyncio.wait_for(
            client.list_ended_meetings(
                start_time=(now - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S"),
                end_time=now.strftime("%Y-%m-%d %H:%M:%S"),
            ),
            timeout=5.0,
        )
    except (TencentAuthError, TencentToolError, asyncio.TimeoutError, Exception) as e:
        logger.warning("tencent list_ended_meetings failed for ir %s: %s", ir_id, e)
        ended = []
    try:
        upcoming = await asyncio.wait_for(client.list_upcoming_meetings(), timeout=5.0)
    except (TencentAuthError, TencentToolError, asyncio.TimeoutError, Exception) as e:
        logger.warning("tencent list_upcoming_meetings failed for ir %s: %s", ir_id, e)
        upcoming = []

    for m in (ended or []) + (upcoming or []):
        start_str = str(m.get("start_time") or "")
        end_str = str(m.get("end_time") or "")
        if "T" not in start_str:
            continue
        try:
            d, rest = start_str.split("T", 1)
            t = rest[:5]
            end_t = end_str.split("T", 1)[1][:5] if "T" in end_str else ""
        except Exception:
            continue
        items.append({
            "date": d,
            "time": t,
            "end_time": end_t,
            "subject": str(m.get("subject") or "（无主题）"),
            "meeting_id": str(m.get("meeting_id") or ""),
        })

    if redis is not None:
        try:
            await redis.setex(cache_key, 300, json.dumps(items, ensure_ascii=False))
        except Exception:
            pass
    return items


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

    # 腾讯会议：当日所有会议合并进事件流
    meetings = await _load_tencent_meetings(db, ir_id)
    cal_date_str = str(cal_date)
    for m in meetings:
        if m["date"] != cal_date_str:
            continue
        events.append(CalendarEvent(
            time=m["time"],
            type="meeting",
            title=m["subject"],
            description=f"腾讯会议 {m['time']}{('～' + m['end_time']) if m['end_time'] else ''}",
            action_label="纪要准备",
            action_prefill=f"为「{m['subject']}」生成会议纪要",
            tencent_meeting_id=m["meeting_id"],
        ))
    events.sort(key=lambda e: e.time)

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

    # 当月范围内的腾讯会议
    meetings = await _load_tencent_meetings(db, current_ir["ir_id"])
    meeting_dates: set[str] = {m["date"] for m in meetings}

    days: dict[str, list[str]] = {}
    for day_num in range(1, last_day_num + 1):
        target_date = date(year, month_num, day_num)
        date_str = str(target_date)
        events = _compute_events_for_day(investors, target_date)
        # Collect unique event types, preserving first-occurrence order
        seen: dict[str, None] = {}
        for e in events:
            seen[e.type] = None
        if date_str in meeting_dates:
            seen["meeting"] = None
        if seen:
            days[date_str] = list(seen.keys())

    return MonthCalendarOut(month=month, days=days)
