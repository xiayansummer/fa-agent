"""Content Agent 分发工具：内容生成类工作流（会议纪要、每日跟进）。"""
from __future__ import annotations
from datetime import datetime, timedelta, time as dtime
from typing import Optional

from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from models.ir_users import IRUser
from models.investors import Investor
from models.interaction_logs import InteractionLog
from models.outreach_records import OutreachRecord
from services import crypto_service
from services.tencent_meeting import TencentMeetingClient, TencentAuthError, TencentToolError
from skills.qmingpian import qmingpian_export_ongoing_lunci

from .base import ToolCtx, start_workflow

AGENT_ROLE = "content"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "start_meeting_minutes_workflow",
            "description": (
                "启动「会议纪要分析」工作流（Content Agent）。三选一参数："
                "tencent_meeting_id（已开云录制的腾讯会议 ID）/ audio_url（已上传的音频公网 URL）/ "
                "transcript（粘贴的文字稿）。成功后返回 thread_id，前端会自动接管显示进度。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tencent_meeting_id": {"type": "string"},
                    "audio_url": {"type": "string"},
                    "transcript": {"type": "string"},
                    "investor_ids": {"type": "array", "items": {"type": "integer"}},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_daily_push_workflow",
            "description": (
                "启动「每日跟进推送生成」工作流（Content Agent）。为指定投资人生成个性化跟进消息草稿。返回 thread_id。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "investor_ids": {"type": "array", "items": {"type": "integer"}},
                    "target_date": {"type": "string", "description": "YYYY-MM-DD"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prepare_meeting_briefing",
            "description": (
                "为即将到来的会议生成会前准备包：拉参会方/机构的历史互动 + 关联 ongoing 项目 + 匹配的腾讯会议条目。"
                "返回结构化数据，**你**（agent）需根据数据整理成 markdown 会前简报："
                "机构背景 / 最近 3 次互动摘要 / 相关项目近况 / 建议议程 3-5 条。"
                "当 IR 说「准备一下 X 的会」「会前简报」「明早 X 点跟 Y 开会前给我准备下」等意图时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "attendee": {"type": "string", "description": "参会方名称（机构名 或 人名）"},
                    "when": {
                        "type": "string",
                        "description": "ISO 时间或自然语言描述（如 '5/8 11:00'）。可选 —— 不填则不主动匹配会议。",
                    },
                },
                "required": ["attendee"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "weekly_review",
            "description": (
                "汇总当前 IR 本周（默认 周一 至 今天）工作记录：拜访机构、互动总数、主要互动摘要、"
                "本周 outreach 草稿、下周待跟进事项。**你**（agent）需根据返回数据写成 markdown 周报。"
                "当 IR 说「本周回顾」「这周做了什么」「周报」「上周总结」等意图时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "YYYY-MM-DD，默认本周一"},
                    "end_date":   {"type": "string", "description": "YYYY-MM-DD，默认今天"},
                },
            },
        },
    },
]


async def _meeting_minutes(args: dict, ctx: ToolCtx) -> dict:
    if not any(args.get(k) for k in ("tencent_meeting_id", "audio_url", "transcript")):
        return {"error": "tencent_meeting_id / audio_url / transcript 至少一个必填"}
    return await start_workflow(ctx.ir_id, "meeting_minutes", {
        "tencent_meeting_id": args.get("tencent_meeting_id"),
        "audio_url": args.get("audio_url"),
        "transcript": args.get("transcript"),
        "investor_ids": args.get("investor_ids"),
    })


async def _daily_push(args: dict, ctx: ToolCtx) -> dict:
    return await start_workflow(ctx.ir_id, "daily_push", {
        "investor_ids": args.get("investor_ids"),
        "target_date": args.get("target_date") or datetime.now().strftime("%Y-%m-%d"),
    })


# ============ private helpers ============

async def _get_tencent_client(db: AsyncSession, ir_id: int) -> Optional[TencentMeetingClient]:
    user = (await db.execute(select(IRUser).where(IRUser.id == ir_id))).scalar_one_or_none()
    if not user or not user.tencent_meeting_token_encrypted:
        return None
    try:
        token = crypto_service.decrypt(user.tencent_meeting_token_encrypted)
    except Exception:
        return None
    return TencentMeetingClient(token=token)


def _serialize_interaction(log: InteractionLog, inv: Optional[Investor]) -> dict:
    return {
        "id": log.id,
        "type": log.type,
        "occurred_at": log.occurred_at.strftime("%Y-%m-%d %H:%M") if log.occurred_at else None,
        "duration_min": log.duration_min,
        "summary": (log.summary or "")[:300],
        "next_followup_at": log.next_followup_at.strftime("%Y-%m-%d %H:%M") if log.next_followup_at else None,
        "investor_id": log.investor_id,
        "investor_name": inv.name if inv else None,
        "agency": inv.agency if inv else None,
        "position": inv.position if inv else None,
    }


# ============ briefing ============

async def _prepare_briefing(args: dict, ctx: ToolCtx) -> dict:
    attendee = (args.get("attendee") or "").strip()
    if not attendee:
        return {"error": "attendee 不能为空"}

    db = ctx.db
    like = f"%{attendee}%"
    rows = (await db.execute(
        select(Investor).where(
            Investor.is_active == True,
            or_(Investor.agency.like(like), Investor.name.like(like)),
        ).limit(20)
    )).scalars().all()
    inv_ids = [r.id for r in rows]
    inv_by_id = {r.id: r for r in rows}

    # 历史互动（这些投资人的最近 5 条）
    interactions = []
    if inv_ids:
        logs = (await db.execute(
            select(InteractionLog)
            .where(InteractionLog.investor_id.in_(inv_ids))
            .order_by(InteractionLog.occurred_at.desc())
            .limit(5)
        )).scalars().all()
        interactions = [_serialize_interaction(l, inv_by_id.get(l.investor_id)) for l in logs]

    # 腾讯会议匹配（subject 含 attendee 关键词）
    matched_meetings: list[dict] = []
    client = await _get_tencent_client(db, ctx.ir_id)
    if client is not None:
        try:
            upcoming = await client.list_upcoming_meetings()
            for m in upcoming:
                subj = m.get("subject") or ""
                if attendee in subj:
                    matched_meetings.append({
                        "meeting_id": str(m.get("meeting_id") or ""),
                        "meeting_code": str(m.get("meeting_code") or m.get("meeting_id_str") or ""),
                        "subject": subj,
                        "start_time": m.get("start_time"),
                        "end_time": m.get("end_time"),
                    })
        except (TencentAuthError, TencentToolError, Exception):
            pass

    # 相关 ongoing 项目：扫所有 ongoing 项目对接清单，过滤含 attendee 的行
    related_projects: list[dict] = []
    try:
        all_ongoing = await qmingpian_export_ongoing_lunci("")
        for row in (all_ongoing.get("contacts") or []):
            blob = f"{row.get('event_name','')} {row.get('agency','')} {row.get('person','')}"
            if attendee in blob:
                related_projects.append({
                    "event_name": row.get("event_name"),
                    "agency": row.get("agency"),
                    "person": row.get("person"),
                })
        related_projects = related_projects[:15]
    except Exception:
        pass

    return {
        "ok": True,
        "attendee": attendee,
        "when_hint": args.get("when") or "",
        "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "investors_in_org": [
            {
                "investor_id": r.id, "name": r.name, "agency": r.agency,
                "position": r.position, "familiarity": r.familiarity,
                "industry_tags": r.industry_tags or [], "stage_pref": r.stage_pref or [],
                "profile_notes": (r.profile_notes or "")[:200],
            } for r in rows[:8]
        ],
        "recent_interactions": interactions,
        "matched_meetings": matched_meetings,
        "related_projects": related_projects,
    }


# ============ weekly review ============

def _resolve_week_range(start_str: Optional[str], end_str: Optional[str]) -> tuple[datetime, datetime]:
    today = datetime.now().date()
    if end_str:
        try:
            end_date = datetime.strptime(end_str, "%Y-%m-%d").date()
        except ValueError:
            end_date = today
    else:
        end_date = today
    if start_str:
        try:
            start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
        except ValueError:
            start_date = today - timedelta(days=today.weekday())
    else:
        start_date = today - timedelta(days=today.weekday())  # 本周一
    return (datetime.combine(start_date, dtime.min),
            datetime.combine(end_date, dtime.max))


async def _weekly_review(args: dict, ctx: ToolCtx) -> dict:
    db = ctx.db
    start_dt, end_dt = _resolve_week_range(args.get("start_date"), args.get("end_date"))

    # 本周互动（join 投资人）
    logs = (await db.execute(
        select(InteractionLog)
        .where(InteractionLog.ir_id == ctx.ir_id,
               InteractionLog.occurred_at.between(start_dt, end_dt))
        .order_by(InteractionLog.occurred_at.desc())
        .limit(60)
    )).scalars().all()

    inv_ids = list({l.investor_id for l in logs})
    inv_by_id: dict[int, Investor] = {}
    if inv_ids:
        rows = (await db.execute(
            select(Investor).where(Investor.id.in_(inv_ids))
        )).scalars().all()
        inv_by_id = {r.id: r for r in rows}

    # 按 agency 聚合：拜访机构
    agency_counts: dict[str, int] = {}
    for l in logs:
        if l.type != "meeting":
            continue
        inv = inv_by_id.get(l.investor_id)
        agency = (inv.agency if inv else None) or "（未知机构）"
        agency_counts[agency] = agency_counts.get(agency, 0) + 1
    agencies_visited = sorted(agency_counts.items(), key=lambda x: -x[1])

    # 互动类型分布
    type_counts: dict[str, int] = {}
    for l in logs:
        type_counts[l.type or "other"] = type_counts.get(l.type or "other", 0) + 1

    # 本周 outreach 草稿/已发
    outreach_rows = (await db.execute(
        select(OutreachRecord)
        .where(OutreachRecord.ir_id == ctx.ir_id,
               OutreachRecord.created_at.between(start_dt, end_dt))
        .order_by(OutreachRecord.created_at.desc())
        .limit(30)
    )).scalars().all()
    outreach_type_counts: dict[str, int] = {}
    outreach_status_counts: dict[str, int] = {}
    for r in outreach_rows:
        outreach_type_counts[r.type or "other"] = outreach_type_counts.get(r.type or "other", 0) + 1
        outreach_status_counts[r.status or "draft"] = outreach_status_counts.get(r.status or "draft", 0) + 1

    # 下周待跟进
    next_week_end = end_dt + timedelta(days=7)
    pending_logs = (await db.execute(
        select(InteractionLog)
        .where(InteractionLog.ir_id == ctx.ir_id,
               InteractionLog.next_followup_at.is_not(None),
               InteractionLog.next_followup_at > end_dt,
               InteractionLog.next_followup_at <= next_week_end)
        .order_by(InteractionLog.next_followup_at.asc())
        .limit(15)
    )).scalars().all()
    pending_inv_ids = list({l.investor_id for l in pending_logs if l.investor_id not in inv_by_id})
    if pending_inv_ids:
        extra = (await db.execute(select(Investor).where(Investor.id.in_(pending_inv_ids)))).scalars().all()
        inv_by_id.update({r.id: r for r in extra})

    return {
        "ok": True,
        "range": {"start": start_dt.strftime("%Y-%m-%d"), "end": end_dt.strftime("%Y-%m-%d")},
        "interaction_total": len(logs),
        "interaction_by_type": type_counts,
        "agencies_visited": [{"agency": a, "meetings": n} for a, n in agencies_visited[:15]],
        "top_interactions": [_serialize_interaction(l, inv_by_id.get(l.investor_id)) for l in logs[:10]],
        "outreach_total": len(outreach_rows),
        "outreach_by_type": outreach_type_counts,
        "outreach_by_status": outreach_status_counts,
        "next_week_pending": [
            {
                "investor_name": (inv_by_id.get(l.investor_id).name if inv_by_id.get(l.investor_id) else None),
                "agency": (inv_by_id.get(l.investor_id).agency if inv_by_id.get(l.investor_id) else None),
                "next_followup_at": l.next_followup_at.strftime("%Y-%m-%d %H:%M"),
                "previous_summary": (l.summary or "")[:120],
            } for l in pending_logs
        ],
    }


_DISPATCH = {
    "start_meeting_minutes_workflow": _meeting_minutes,
    "start_daily_push_workflow":      _daily_push,
    "prepare_meeting_briefing":       _prepare_briefing,
    "weekly_review":                  _weekly_review,
}


async def dispatch(name: str, args: dict, ctx: ToolCtx) -> dict:
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"error": f"content: 未知工具 {name}"}
    return await fn(args, ctx)
