"""List Agent 分发工具：候选投资人推荐。"""
from __future__ import annotations
from .base import ToolCtx, start_workflow

AGENT_ROLE = "list"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "start_smart_list_workflow",
            "description": (
                "启动「候选投资人推荐」工作流（List Agent）。按 criteria（行业/阶段/关注领域等）"
                "从企名片+本地库捞候选并排序。"
            ),
            "parameters": {
                "type": "object",
                "properties": {"criteria": {"type": "string"}},
                "required": ["criteria"],
            },
        },
    },
]


async def _smart_list(args: dict, ctx: ToolCtx) -> dict:
    criteria = (args.get("criteria") or "").strip()
    if not criteria:
        return {"error": "criteria 必填"}
    return await start_workflow(ctx.ir_id, "smart_list", {"criteria": criteria})


_DISPATCH = {"start_smart_list_workflow": _smart_list}


async def dispatch(name: str, args: dict, ctx: ToolCtx) -> dict:
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"error": f"list: 未知工具 {name}"}
    return await fn(args, ctx)
