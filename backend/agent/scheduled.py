"""定时任务的 headless 工作流执行（无人工审核，直接落草稿）。

celery beat 触发的 daily_push / milestone_outreach 不走 HTTP 自调 FastAPI
（那条路鉴权+端口都不通），而是在 worker 进程内直接调各 workflow 的节点函数，
跳过 review 中断，按 status=draft 落库供 IR 次日在「草稿历史」批量审。

投资人是共享池（无 owner 列），所以按 IR 维度跑：每个活跃 IR 用
_my_investor_ids 把范围限定到「他自己的投资人」，草稿才归属正确、对该 IR 可见。
"""
from __future__ import annotations
import logging
import uuid
from datetime import date

from sqlalchemy import select

from database import AsyncSessionLocal
from models.ir_users import IRUser
from models.investors import Investor
from models.interaction_logs import InteractionLog
from models.outreach_records import OutreachRecord
from models.ir_investor_membership import IrInvestorMembership
from agent.workflows import daily_push as dp
from agent.workflows import milestone_outreach as mo

logger = logging.getLogger(__name__)


async def _active_ir_ids() -> list[int]:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(IRUser.id).where(IRUser.is_active == True)
        )).scalars().all()
    return list(rows)


async def _my_investor_ids(db, ir_id: int) -> list[int]:
    """该 IR 的投资人。主源 ir_investor_membership 表 + 旧 logs 推导兜底。
    与 api.calendar._my_investor_ids 同口径（此处内联避免 agent 依赖 api 层）。"""
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


async def run_daily_push_for_all_irs(target_date: str | None = None) -> dict:
    """每个活跃 IR：在其投资人范围内生成 daily_push 草稿（status=draft）。"""
    target = target_date or date.today().isoformat()
    summary = {"irs": 0, "with_drafts": 0}
    for ir_id in await _active_ir_ids():
        summary["irs"] += 1
        async with AsyncSessionLocal() as db:
            my_ids = await _my_investor_ids(db, ir_id)
        if not my_ids:
            continue
        state = {
            "thread_id": f"sched-dp-{ir_id}-{uuid.uuid4().hex[:8]}",
            "ir_id": ir_id,
            "task_type": "daily_push",
            "target_date": target,
            "investor_ids": my_ids,
            "auto_draft": True,
            "skills_called": [],
        }
        try:
            state.update(await dp.fetch_events_node(state))
            if not state.get("events"):
                continue
            state.update(await dp.fetch_profiles_node(state))
            state.update(await dp.generate_node(state))
            await dp.save_node(state)
            summary["with_drafts"] += 1
        except Exception:
            logger.exception("scheduled daily_push failed ir=%s", ir_id)
    logger.info("scheduled daily_push done: %s", summary)
    return summary


async def run_milestone_outreach_for_all_irs(target_date: str | None = None) -> dict:
    """每个活跃 IR：找其投资人今日生日/入职纪念日，逐个生成 milestone 草稿（status=draft）。"""
    target = date.fromisoformat(target_date) if target_date else date.today()
    summary = {"irs": 0, "drafts": 0}
    for ir_id in await _active_ir_ids():
        summary["irs"] += 1
        async with AsyncSessionLocal() as db:
            my_ids = await _my_investor_ids(db, ir_id)
            if not my_ids:
                continue
            investors = (await db.execute(
                select(Investor).where(Investor.id.in_(my_ids), Investor.is_active == True)
            )).scalars().all()
            ir_row = (await db.execute(
                select(IRUser).where(IRUser.id == ir_id)
            )).scalar_one_or_none()
            ir_name = ir_row.name if ir_row else "IR"

        milestones: list[tuple[int, str]] = []
        for inv in investors:
            if inv.birthday and inv.birthday.month == target.month and inv.birthday.day == target.day:
                milestones.append((inv.id, "birthday"))
            if inv.join_agency_date and inv.join_agency_date.month == target.month and inv.join_agency_date.day == target.day:
                milestones.append((inv.id, "join_agency"))

        for investor_id, mtype in milestones:
            state = {
                "thread_id": f"sched-ms-{investor_id}-{uuid.uuid4().hex[:8]}",
                "ir_id": ir_id,
                "task_type": "milestone_outreach",
                "investor_id": investor_id,
                "milestone_type": mtype,
                "ir_name": ir_name,
                "skills_called": [],
            }
            try:
                state.update(await mo.fetch_investor_node(state))
                if state.get("error"):
                    continue
                state.update(await mo.generate_node(state))
                state["final"] = state.get("draft")  # headless：final=draft，save_node 用 final 落 content
                await mo.save_node(state)
                summary["drafts"] += 1
            except Exception:
                logger.exception("scheduled milestone failed ir=%s inv=%s", ir_id, investor_id)
    logger.info("scheduled milestone_outreach done: %s", summary)
    return summary


async def run_schedule_reminders() -> dict:
    """日程提醒：扫 calendar_events 里「未来 30 分钟内开始、未提醒、IR 有订阅配额」的日程，
    发微信订阅消息（服务通知），发完打 reminded_at 标记并扣配额。

    时间基准：用户填的 event_date/start_time 是北京墙上时间，容器可能是 UTC，
    所以这里显式用 Asia/Shanghai 计算"现在"，不依赖容器时区。
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from config import settings
    from models.calendar_events import CalendarEventRow
    from models.wx_sub_quota import WxSubQuota
    from services.wx_notify import send_schedule_reminder

    now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    window_end = now + timedelta(minutes=30)
    # no_quota=本地没攒配额/没openid；wx_43101=调到微信但用户侧无真实订阅授权
    summary = {"checked": 0, "sent": 0, "no_quota": 0, "wx_43101": 0, "failed": 0}

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(CalendarEventRow).where(
                CalendarEventRow.reminded_at.is_(None),
                CalendarEventRow.start_time.is_not(None),
                CalendarEventRow.event_date.in_([now.date(), window_end.date()]),
            )
        )).scalars().all()

        due = []
        for ev in rows:
            try:
                h, m = ev.start_time.split(":")
                ev_dt = datetime.combine(ev.event_date, datetime.min.time()).replace(
                    hour=int(h), minute=int(m))
            except (ValueError, AttributeError):
                continue
            # 已经开始超过 2 分钟的不再提醒（错过窗口），未来 30 分钟内的提醒
            if now - timedelta(minutes=2) <= ev_dt <= window_end:
                due.append((ev, ev_dt))
        summary["checked"] = len(due)
        if not due:
            return summary

        ir_ids = {ev.ir_id for ev, _ in due}
        users = (await db.execute(
            select(IRUser).where(IRUser.id.in_(ir_ids))
        )).scalars().all()
        openid_by_ir = {u.id: u.wechat_openid for u in users if u.wechat_openid}
        quotas = (await db.execute(
            select(WxSubQuota).where(
                WxSubQuota.ir_id.in_(ir_ids),
                WxSubQuota.template_id == settings.wx_schedule_tmpl_id,
            )
        )).scalars().all()
        quota_by_ir = {q.ir_id: q for q in quotas}

        # 关联投资人名（模板 thing11「客户名称」字段）
        inv_ids = {ev.investor_id for ev, _ in due if ev.investor_id}
        inv_name_by_id: dict[int, str] = {}
        if inv_ids:
            inv_rows = (await db.execute(
                select(Investor).where(Investor.id.in_(inv_ids))
            )).scalars().all()
            inv_name_by_id = {i.id: i.name for i in inv_rows}

        for ev, ev_dt in due:
            openid = openid_by_ir.get(ev.ir_id)
            quota = quota_by_ir.get(ev.ir_id)
            if not openid or not quota or (quota.times or 0) <= 0:
                summary["no_quota"] += 1
                continue
            try:
                resp = await send_schedule_reminder(
                    openid=openid,
                    title=ev.title,
                    time_str=ev_dt.strftime("%Y-%m-%d %H:%M"),
                    note=ev.notes or "",
                    investor_name=inv_name_by_id.get(ev.investor_id or 0, ""),
                    location=ev.location or "",
                    page=f"pages/calendar-day/index?date={ev.event_date.isoformat()}",
                )
            except Exception:
                logger.exception("schedule reminder send failed event=%s", ev.id)
                summary["failed"] += 1
                continue
            code = resp.get("errcode")
            if code == 0:
                ev.reminded_at = now
                quota.times = (quota.times or 0) - 1
                summary["sent"] += 1
            elif code == 43101:
                # 用户侧没有可用订阅（从未点过「允许」/拒收/已用完）——本地配额作废，避免反复打无效请求
                quota.times = 0
                summary["wx_43101"] += 1
            else:
                logger.warning("schedule reminder errcode=%s errmsg=%s event=%s",
                               code, resp.get("errmsg"), ev.id)
                summary["failed"] += 1
        await db.commit()

    logger.info("schedule reminders done: %s", summary)
    return summary


async def _dispose_async_singletons() -> None:
    """celery prefork 子进程里每次任务都是一次全新的 asyncio.run() loop——
    模块级单例（SQLAlchemy 连接池 / redis 客户端）里的连接还绑着上一个 loop，
    下次任务复用就炸 "got Future attached to a different loop"
    （2026-06-12 提醒任务成功/失败交替的根因）。
    每次任务收尾把 loop 绑定资源全部关掉/重置，下个 loop 重建，连接成本可忽略。"""
    import asyncio as _asyncio
    from database import engine
    try:
        await engine.dispose()
    except Exception:
        logger.warning("scheduled cleanup: engine.dispose failed", exc_info=True)
    import redis_client
    try:
        if redis_client._redis is not None:
            await redis_client._redis.aclose()
    except Exception:
        pass
    redis_client._redis = None
    redis_client._lock = _asyncio.Lock()  # asyncio.Lock 首次 await 后也会绑 loop


async def run_with_cleanup(fn, *args, **kwargs):
    """worker 任务统一入口：跑完（无论成败）清理 loop 绑定单例。"""
    try:
        return await fn(*args, **kwargs)
    finally:
        await _dispose_async_singletons()
