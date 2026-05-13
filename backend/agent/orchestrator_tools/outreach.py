"""Outreach Agent 分发工具：里程碑触达 / 外联消息。"""
from __future__ import annotations
from sqlalchemy import select
from models.ir_users import IRUser
from .base import ToolCtx, start_workflow

AGENT_ROLE = "outreach"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "start_milestone_outreach_workflow",
            "description": (
                "启动「里程碑触达」工作流（Outreach Agent）。为某投资人的生日/入职纪念/首次见面纪念生成祝贺消息。"
                "milestone_type ∈ {'birthday','join_agency','first_meeting'}。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "investor_id": {"type": "integer"},
                    "milestone_type": {"type": "string"},
                },
                "required": ["investor_id", "milestone_type"],
            },
        },
    },
]


_VALID_MILESTONE = {"birthday", "join_agency", "first_meeting"}


async def _milestone_outreach(args: dict, ctx: ToolCtx) -> dict:
    inv_id = args.get("investor_id")
    mtype = (args.get("milestone_type") or "").strip()
    if not inv_id or mtype not in _VALID_MILESTONE:
        return {"error": "investor_id 必填且 milestone_type ∈ birthday/join_agency/first_meeting"}
    ir_row = (await ctx.db.execute(select(IRUser).where(IRUser.id == ctx.ir_id))).scalar_one_or_none()
    return await start_workflow(ctx.ir_id, "milestone_outreach", {
        "investor_id": inv_id,
        "milestone_type": mtype,
        "ir_name": ir_row.name if ir_row else "IR",
    })


_DISPATCH = {"start_milestone_outreach_workflow": _milestone_outreach}


async def dispatch(name: str, args: dict, ctx: ToolCtx) -> dict:
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"error": f"outreach: 未知工具 {name}"}
    return await fn(args, ctx)
