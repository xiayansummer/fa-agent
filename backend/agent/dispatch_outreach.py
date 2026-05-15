"""Outreach Agent 派发实现 —— 从 meeting_minutes workflow 抽出来独立模块，
让 celery 任务能直接调用（不绑定 LangGraph state）。"""
from __future__ import annotations
import logging
from sqlalchemy import select
from database import AsyncSessionLocal
from harness.skill_registry import skill_registry
from harness.prompt_registry import registry as prompt_registry
from models.investors import Investor
from models.outreach_records import OutreachRecord

logger = logging.getLogger(__name__)

# (action_type, prompt_key, label for tracing)
DISPATCH_RULES = [
    ("meeting_request",    "outreach_agent.meeting_request",    "outreach_预约"),
    ("investor_milestone", "outreach_agent.investor_milestone", "outreach_milestone提醒"),
]


async def dispatch_outreach_impl(
    ir_id: int,
    investor_ids: list[int],
    action_items: list[dict],
    summary: str,
) -> dict:
    """对每个 (investor × action) 调 LLM 生成 outreach 草稿，写入 OutreachRecord(draft)。

    被 LangGraph workflow 和 celery task 共用 —— 没有任何 state 依赖，纯参数驱动。
    """
    target_types = {r[0] for r in DISPATCH_RULES}
    target_items = [it for it in (action_items or []) if it.get("type") in target_types]
    if not target_items or not investor_ids:
        return {"drafts_by_kind": {}}

    async with AsyncSessionLocal() as db:
        inv_rows = (await db.execute(
            select(Investor).where(Investor.id.in_(investor_ids))
        )).scalars().all()
        inv_map = {inv.id: inv for inv in inv_rows}

        drafts_by_kind: dict[str, int] = {}
        for inv_id in investor_ids:
            inv = inv_map.get(inv_id)
            if not inv:
                continue
            for action in target_items:
                rule = next((r for r in DISPATCH_RULES if r[0] == action.get("type")), None)
                if not rule:
                    continue
                _, prompt_key, kind_label = rule
                variables = {
                    "investor_name": inv.name,
                    "investor_agency": (inv.agency or "").replace("{", "{{").replace("}", "}}"),
                    "minutes_summary": (summary or "").replace("{", "{{").replace("}", "}}"),
                    "action_title": action["title"].replace("{", "{{").replace("}", "}}"),
                    "milestone_title": action["title"].replace("{", "{{").replace("}", "}}"),
                    "due_date": action["due_date"],
                }
                try:
                    context = prompt_registry.get(prompt_key, variables=variables)
                    msg = await skill_registry.call("Claude.生成内容", context=context, max_tokens=400)
                    msg = (msg or "").strip()
                    if not msg:
                        continue
                    db.add(OutreachRecord(
                        investor_id=inv_id,
                        ir_id=ir_id,
                        type="milestone_message",
                        content=msg,
                        status="draft",
                    ))
                    drafts_by_kind[kind_label] = drafts_by_kind.get(kind_label, 0) + 1
                except Exception as e:
                    logger.warning("outreach dispatch %s failed: %s", action.get("type"), e)
        await db.commit()

    return {"drafts_by_kind": drafts_by_kind}
