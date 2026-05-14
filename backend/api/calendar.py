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
from models.interaction_logs import InteractionLog
from models.ir_users import IRUser
from models.calendar_dismissals import CalendarDismissal
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
    event_key: str = ""                       # 用于 IR 主动 dismiss 时定位事件

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


def _compute_events_for_day(
    investors,
    target_date: date,
    action_followups: Optional[dict[int, list[tuple[int, str, str]]]] = None,
    dismissed_keys: Optional[set[str]] = None,
) -> list[CalendarEvent]:
    """Return all CalendarEvent objects for a given date.
    action_followups: investor_id → list of (log_id, action_title, action_summary).
    dismissed_keys: 当日 IR 已经 dismiss 的 event_key 集合，命中即跳过。
    """
    events: list[CalendarEvent] = []
    inv_by_id = {inv.id: inv for inv in investors}
    dismissed = dismissed_keys or set()

    if action_followups:
        for inv_id, actions in action_followups.items():
            inv = inv_by_id.get(inv_id)
            if not inv:
                continue
            for log_id, title, summary in actions:
                key = f"action:{log_id}"
                if key in dismissed:
                    continue
                events.append(CalendarEvent(
                    time="09:00",
                    type="followup",
                    title=title,
                    description=summary or "来自近期会议纪要的待办",
                    investor_id=inv.id,
                    investor_name=inv.name,
                    action_label="执行",
                    action_prefill=f"帮我跟进{inv.name}：{title}",
                    event_key=key,
                ))

    for inv in investors:
        if inv.last_interaction_at:
            days_since = (target_date - inv.last_interaction_at.date()).days
            if days_since >= 14 and not (action_followups and inv.id in action_followups):
                key = f"followup:{inv.id}"
                if key not in dismissed:
                    events.append(CalendarEvent(
                        time="09:00",
                        type="followup",
                        title=f"跟进{inv.name}（{inv.agency or ''}）",
                        description=f"上次互动 {days_since} 天前",
                        investor_id=inv.id,
                        investor_name=inv.name,
                        action_label="执行",
                        action_prefill=f"帮我跟进{inv.name}，生成一条行业推送",
                        event_key=key,
                    ))

        if inv.birthday and inv.birthday.month == target_date.month and inv.birthday.day == target_date.day:
            key = f"birthday:{inv.id}"
            if key not in dismissed:
                events.append(CalendarEvent(
                    time="10:00",
                    type="milestone",
                    title=f"{inv.name} 生日",
                    description=f"{inv.agency or ''} · {inv.position or ''}",
                    investor_id=inv.id,
                    investor_name=inv.name,
                    action_label="审核祝贺",
                    action_prefill=f"为{inv.name}生成生日祝贺消息",
                    event_key=key,
                ))

        if inv.join_agency_date:
            years = target_date.year - inv.join_agency_date.year
            if (years > 0
                    and inv.join_agency_date.month == target_date.month
                    and inv.join_agency_date.day == target_date.day):
                key = f"anniversary:{inv.id}:{years}"
                if key not in dismissed:
                    events.append(CalendarEvent(
                        time="10:30",
                        type="milestone",
                        title=f"{inv.name} 加入{inv.agency or ''} {years} 周年",
                        description="里程碑节点，建议发送祝贺",
                        investor_id=inv.id,
                        investor_name=inv.name,
                        action_label="审核祝贺",
                        action_prefill=f"为{inv.name}生成加入{inv.agency or ''}{years}周年祝贺消息",
                        event_key=key,
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

    # 当日的 action-item followup（来自 interaction_logs.next_followup_at 命中）
    from datetime import datetime as _dt
    day_start = _dt.combine(cal_date, _dt.min.time())
    day_end = _dt.combine(cal_date, _dt.max.time())
    fl_rows = (await db.execute(
        select(InteractionLog).where(
            InteractionLog.next_followup_at.is_not(None),
            InteractionLog.next_followup_at >= day_start,
            InteractionLog.next_followup_at <= day_end,
            InteractionLog.ir_id == ir_id,
        )
    )).scalars().all()
    action_followups: dict[int, list[tuple[int, str, str]]] = {}
    for log in fl_rows:
        title = f"跟进 action：{(log.summary or '')[:30]}"
        action_followups.setdefault(log.investor_id, []).append((log.id, title, log.summary or ""))

    # 当日 IR 主动 dismiss 的 event_key 集合
    dism_rows = (await db.execute(
        select(CalendarDismissal.event_key).where(
            CalendarDismissal.ir_id == ir_id,
            CalendarDismissal.event_date == cal_date,
        )
    )).scalars().all()
    dismissed_keys = set(dism_rows)

    events = _compute_events_for_day(
        investors, cal_date,
        action_followups=action_followups,
        dismissed_keys=dismissed_keys,
    )

    # 腾讯会议：当日所有会议合并进事件流
    meetings = await _load_tencent_meetings(db, ir_id)
    cal_date_str = str(cal_date)
    for m in meetings:
        if m["date"] != cal_date_str:
            continue
        key = f"meeting:{m['meeting_id']}"
        if key in dismissed_keys:
            continue
        events.append(CalendarEvent(
            time=m["time"],
            type="meeting",
            title=m["subject"],
            description=f"腾讯会议 {m['time']}{('～' + m['end_time']) if m['end_time'] else ''}",
            action_label="纪要准备",
            action_prefill=f"为「{m['subject']}」生成会议纪要",
            tencent_meeting_id=m["meeting_id"],
            event_key=key,
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

    # 当月范围的 action-item followup（一次性 prefetch 按日期分组）
    from datetime import datetime as _dt
    month_start = _dt.combine(date(year, month_num, 1), _dt.min.time())
    month_end = _dt.combine(date(year, month_num, last_day_num), _dt.max.time())
    fl_rows = (await db.execute(
        select(InteractionLog).where(
            InteractionLog.next_followup_at.is_not(None),
            InteractionLog.next_followup_at >= month_start,
            InteractionLog.next_followup_at <= month_end,
            InteractionLog.ir_id == current_ir["ir_id"],
        )
    )).scalars().all()
    followups_by_date: dict[str, dict[int, list[tuple[int, str, str]]]] = {}
    for log in fl_rows:
        ds = log.next_followup_at.date().isoformat()
        title = f"跟进 action：{(log.summary or '')[:30]}"
        followups_by_date.setdefault(ds, {}).setdefault(log.investor_id, []).append(
            (log.id, title, log.summary or "")
        )

    # 整月 dismiss 一次性查（按日分组）
    dism_rows = (await db.execute(
        select(CalendarDismissal.event_date, CalendarDismissal.event_key).where(
            CalendarDismissal.ir_id == current_ir["ir_id"],
            CalendarDismissal.event_date >= date(year, month_num, 1),
            CalendarDismissal.event_date <= date(year, month_num, last_day_num),
        )
    )).all()
    dismissed_by_date: dict[str, set[str]] = {}
    for d, k in dism_rows:
        dismissed_by_date.setdefault(d.isoformat(), set()).add(k)

    # 当月 meeting key 按日聚合，便于 month dot 计算时也过滤掉已 dismiss 的会议
    meetings_by_date: dict[str, list[dict]] = {}
    for m in meetings:
        meetings_by_date.setdefault(m["date"], []).append(m)

    days: dict[str, list[str]] = {}
    for day_num in range(1, last_day_num + 1):
        target_date = date(year, month_num, day_num)
        date_str = str(target_date)
        dismissed_today = dismissed_by_date.get(date_str, set())
        events = _compute_events_for_day(
            investors, target_date,
            action_followups=followups_by_date.get(date_str),
            dismissed_keys=dismissed_today,
        )
        seen: dict[str, None] = {}
        for e in events:
            seen[e.type] = None
        # 同步过滤 meeting：当日是否有「未被 dismiss」的会议
        if any(f"meeting:{m['meeting_id']}" not in dismissed_today
               for m in meetings_by_date.get(date_str, [])):
            seen["meeting"] = None
        if seen:
            days[date_str] = list(seen.keys())

    return MonthCalendarOut(month=month, days=days)


class DismissRequest(BaseModel):
    event_key: str
    event_date: str  # YYYY-MM-DD


@router.post("/dismiss")
async def dismiss_event(
    body: DismissRequest,
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    """IR 把某天某条事件从日历视图删掉。仅影响视图，不修改源数据
    （腾讯会议不会真取消、interaction_log 不会修改）。再次出现需要 IR 主动 chat 重新创建。"""
    try:
        d = date.fromisoformat(body.event_date)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid event_date: {body.event_date}")
    key = (body.event_key or "").strip()
    if not key:
        raise HTTPException(status_code=422, detail="event_key required")
    row = CalendarDismissal(
        ir_id=current_ir["ir_id"], event_key=key, event_date=d,
    )
    db.add(row)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        # unique 冲突 = 已 dismiss 过，视为成功
    return {"ok": True, "event_key": key, "event_date": body.event_date}
