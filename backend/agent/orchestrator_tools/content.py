"""Content Agent 分发工具：内容生成类工作流（会议纪要、每日跟进）。"""
from __future__ import annotations
from datetime import datetime
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


_DISPATCH = {
    "start_meeting_minutes_workflow": _meeting_minutes,
    "start_daily_push_workflow":      _daily_push,
}


async def dispatch(name: str, args: dict, ctx: ToolCtx) -> dict:
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"error": f"content: 未知工具 {name}"}
    return await fn(args, ctx)
