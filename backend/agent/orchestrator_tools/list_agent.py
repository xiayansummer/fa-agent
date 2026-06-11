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
                "启动「投资机构名单推荐」工作流（List Agent）。当 IR 说「给 XX 项目出个名单/推荐机构」时调用。"
                "candidates 来自企名片全公司活跃对接池 + 共享投资人库（不限于当前 IR 自己的人脉），"
                "按企名片纪要/历史推荐/标签等证据为项目精排出机构名单，并标注本所已有联系人。"
                "criteria 请把 IR 提到的关键信息都带上：行业赛道、融资阶段/轮次、金额、商业模式、地域等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {"criteria": {"type": "string", "description": "项目需求描述（赛道/阶段/金额/特殊要求），尽量完整"}},
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
