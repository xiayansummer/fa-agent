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
from models.outreach_records import OutreachRecord
from models.ir_investor_membership import IrInvestorMembership
from models.calendar_events import CalendarEventRow
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
    event_id: int = 0                         # 仅 type=schedule 时有值，用于编辑/删除

class DailyCalendarOut(BaseModel):
    date: str
    ir_id: int
    events: list[CalendarEvent]

class MonthCalendarOut(BaseModel):
    month: str          # "YYYY-MM"
    days: dict[str, list[str]]  # date_str → list of unique event types


async def _my_investor_ids(db: AsyncSession, ir_id: int) -> list[int]:
    """该 IR 「自己的」投资人。
    主源：ir_investor_membership（"+ 新增 / + 加入" 时显式写入）。
    兜底：InteractionLog + OutreachRecord 推导（防 backfill 漏 / 工作流新增的关系还没回写 membership）。
    用于隔离不同 IR 的日历视图和投资人库，避免数据全表泄漏。"""
    rows_m = (await db.execute(
        select(IrInvestorMembership.investor_id).where(IrInvestorMembership.ir_id == ir_id)
    )).scalars().all()
    rows_a = (await db.execute(
        select(InteractionLog.investor_id).where(InteractionLog.ir_id == ir_id).distinct()
    )).scalars().all()
    rows_b = (await db.execute(
        select(OutreachRecord.investor_id).where(OutreachRecord.ir_id == ir_id).distinct()
    )).scalars().all()
    return [i for i in {*rows_m, *rows_a, *rows_b} if i is not None]


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
        # 必须传 Unix 整数时间戳：曾用 strftime 字符串，腾讯静默把窗口拉早 2 天，
        # 当天的 ended 会议（开过/结束的）会完全拿不到，看起来像「今天 13:30 这场没收到」。
        ended = await asyncio.wait_for(
            client.list_ended_meetings(
                start_time=int((now - timedelta(days=60)).timestamp()),
                end_time=int(now.timestamp()),
                page_size=100,
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

    # ended + upcoming 可能同时包含同一场（刚结束的会议腾讯短时间内会出现在两边），
    # 按 (meeting_id, date, time) 去重——递归会议同 meeting_id 不同实例时间能保留
    seen: set[tuple[str, str, str]] = set()
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
        mid = str(m.get("meeting_id") or "")
        key = (mid, d, t)
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "date": d,
            "time": t,
            "end_time": end_t,
            "subject": str(m.get("subject") or "（无主题）"),
            "meeting_id": mid,
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

    my_ids = await _my_investor_ids(db, ir_id)
    investors = []
    if my_ids:
        result = await db.execute(
            select(Investor).where(
                Investor.is_active == True,
                Investor.id.in_(my_ids),
            )
        )
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

    # IR 自由日程（calendar_events，按 IR 隔离）—— 一等公民，可编辑/删除
    sched_rows = (await db.execute(
        select(CalendarEventRow).where(
            CalendarEventRow.ir_id == ir_id,
            CalendarEventRow.event_date == cal_date,
        )
    )).scalars().all()
    for row in sched_rows:
        events.append(_schedule_row_to_event(row))

    events.sort(key=lambda e: e.time)

    return DailyCalendarOut(date=str(cal_date), ir_id=ir_id, events=events)


def _schedule_row_to_event(row: CalendarEventRow) -> CalendarEvent:
    """把 calendar_events 行转成日历事件（type=schedule）。"""
    t = row.start_time or "全天"
    desc_parts = []
    if row.start_time:
        desc_parts.append(row.start_time + (f"～{row.end_time}" if row.end_time else ""))
    if row.location:
        desc_parts.append(row.location)
    if row.notes:
        desc_parts.append(row.notes)
    return CalendarEvent(
        time=t if row.start_time else "00:00",  # 全天排在最前
        type="schedule",
        title=row.title,
        description=" · ".join(desc_parts) or "我的日程",
        investor_id=row.investor_id or 0,
        action_label="编辑",
        action_prefill="",
        event_key=f"schedule:{row.id}",
        event_id=row.id,
    )


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

    ir_id = current_ir["ir_id"]
    my_ids = await _my_investor_ids(db, ir_id)
    investors = []
    if my_ids:
        result = await db.execute(
            select(Investor).where(
                Investor.is_active == True,
                Investor.id.in_(my_ids),
            )
        )
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

    # 当月 IR 自由日程（calendar_events）按日聚合，用于月历圆点
    sched_rows = (await db.execute(
        select(CalendarEventRow.event_date).where(
            CalendarEventRow.ir_id == current_ir["ir_id"],
            CalendarEventRow.event_date >= date(year, month_num, 1),
            CalendarEventRow.event_date <= date(year, month_num, last_day_num),
        )
    )).scalars().all()
    schedule_dates: set[str] = {d.isoformat() for d in sched_rows}

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
        # IR 自由日程圆点
        if date_str in schedule_dates:
            seen["schedule"] = None
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


# ============ IR 自由日程 手动 CRUD（calendar_events，按 IR 隔离）============

class ScheduleIn(BaseModel):
    title: str
    date: str                          # YYYY-MM-DD
    start_time: Optional[str] = None   # HH:MM
    end_time: Optional[str] = None     # HH:MM
    investor_id: Optional[int] = None
    location: Optional[str] = None
    notes: Optional[str] = None


def _norm_hm(v: Optional[str]) -> Optional[str]:
    v = (v or "").strip()
    if not v:
        return None
    parts = v.split(":")
    try:
        h = int(parts[0]); m = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return f"{h:02d}:{m:02d}"


def _schedule_row_out(row: CalendarEventRow) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "date": row.event_date.isoformat(),
        "start_time": row.start_time,
        "end_time": row.end_time,
        "investor_id": row.investor_id,
        "location": row.location,
        "notes": row.notes,
        "source": row.source,
    }


@router.get("/events/{event_id}")
async def get_schedule_event(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    """取单条日程原始字段（编辑态用）。owner check：只能看自己的。"""
    row = (await db.execute(
        select(CalendarEventRow).where(
            CalendarEventRow.id == event_id,
            CalendarEventRow.ir_id == current_ir["ir_id"],
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="日程不存在或无权限")
    return _schedule_row_out(row)


@router.post("/events")
async def create_schedule_event(
    body: ScheduleIn,
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    """IR 手动新建一条日程。按 IR 隔离：ir_id 取当前登录用户。"""
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="title 必填")
    try:
        ev_date = date.fromisoformat((body.date or "").strip())
    except ValueError:
        raise HTTPException(status_code=422, detail=f"date 格式无效（YYYY-MM-DD）：{body.date}")
    row = CalendarEventRow(
        ir_id=current_ir["ir_id"],
        investor_id=body.investor_id or None,
        title=title,
        event_date=ev_date,
        start_time=_norm_hm(body.start_time),
        end_time=_norm_hm(body.end_time),
        location=(body.location or "").strip() or None,
        notes=(body.notes or "").strip() or None,
        source="manual",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"ok": True, **_schedule_row_out(row)}


@router.put("/events/{event_id}")
async def update_schedule_event(
    event_id: int,
    body: ScheduleIn,
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    """编辑日程。owner check：只能改自己的（ir_id 匹配），否则 404。"""
    row = (await db.execute(
        select(CalendarEventRow).where(
            CalendarEventRow.id == event_id,
            CalendarEventRow.ir_id == current_ir["ir_id"],
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="日程不存在或无权限")
    if body.title is not None and body.title.strip():
        row.title = body.title.strip()
    if body.date:
        try:
            row.event_date = date.fromisoformat(body.date.strip())
        except ValueError:
            raise HTTPException(status_code=422, detail=f"date 格式无效：{body.date}")
    row.start_time = _norm_hm(body.start_time)
    row.end_time = _norm_hm(body.end_time)
    row.investor_id = body.investor_id or None
    row.location = (body.location or "").strip() or None
    row.notes = (body.notes or "").strip() or None
    await db.commit()
    await db.refresh(row)
    return {"ok": True, **_schedule_row_out(row)}


@router.delete("/events/{event_id}")
async def delete_schedule_event(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    """删除日程（真删，不是 dismiss）。owner check：只能删自己的。"""
    row = (await db.execute(
        select(CalendarEventRow).where(
            CalendarEventRow.id == event_id,
            CalendarEventRow.ir_id == current_ir["ir_id"],
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="日程不存在或无权限")
    await db.delete(row)
    await db.commit()
    return {"ok": True, "deleted": True, "id": event_id}
