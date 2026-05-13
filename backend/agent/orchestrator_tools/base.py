"""Shared types + helpers for the tool-by-agent modules."""
from __future__ import annotations
import asyncio as _asyncio
import uuid
from dataclasses import dataclass
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession


THREAD_OWNER_TTL = 3600  # max workflow + IR-review pause window


@dataclass
class ToolCtx:
    """Per-tool-call context passed into each module's dispatch()."""
    ir_id: int
    db: AsyncSession
    ir_name: Optional[str] = None


async def start_workflow(ir_id: int, task_type: str, state_overrides: dict) -> dict:
    """统一启动 LangGraph workflow（fire-and-forget）。所有 start_* 工具共用。"""
    from redis_client import get_redis
    from agent.runner import run

    thread_id = str(uuid.uuid4())
    state: dict = {
        "thread_id": thread_id,
        "ir_id": ir_id,
        "task_type": task_type,
        "meeting_id": None, "audio_url": None, "transcript": None,
        "tencent_meeting_id": None,
        "investor_ids": None, "investor_profiles": None,
        "target_date": None, "events": None,
        "criteria": None, "candidate_ids": None,
        "investor_id": None, "milestone_type": None, "ir_name": None,
        "draft": None, "final": None, "ir_action": None,
        "prompt_version": None, "skills_called": [], "error": None,
        "briefing_signals": None, "generated_messages_json": None,
        "interaction_summary": None,
        "action_items": None,
    }
    state.update(state_overrides)
    redis = await get_redis()
    await redis.setex(f"agent:thread:{thread_id}:owner", THREAD_OWNER_TTL, str(ir_id))
    _asyncio.create_task(run(task_type, state, thread_id))
    return {"ok": True, "thread_id": thread_id, "task_type": task_type}
