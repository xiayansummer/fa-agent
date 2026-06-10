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
