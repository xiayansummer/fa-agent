from __future__ import annotations
import asyncio
import logging
from typing import Any

from langgraph.types import Command

logger = logging.getLogger(__name__)

# AsyncRedisSaver 需要 running event loop 才能 __init__，所以不能在模块加载时构造。
# Workflows 改成注册 *uncompiled builder*，runner 在 FastAPI startup 钩子里
# 创建 checkpointer + 编译所有 graph。
_checkpointer: Any | None = None
_setup_lock = asyncio.Lock()
_builders: dict[str, Any] = {}
_graphs: dict[str, Any] = {}


def register_builder(task_type: str, builder: Any) -> None:
    """Workflow 在 import 时调用，登记一个未编译的 StateGraph builder。"""
    _builders[task_type] = builder


async def setup_checkpointer() -> None:
    """FastAPI startup 调用一次：建 AsyncRedisSaver + 在 Redis 建索引 + 编译所有 graph。"""
    global _checkpointer
    async with _setup_lock:
        if _checkpointer is not None:
            return
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver
        from config import settings

        saver = AsyncRedisSaver(redis_url=settings.redis_url)
        await saver.asetup()
        _checkpointer = saver
        for task_type, builder in _builders.items():
            _graphs[task_type] = builder.compile(checkpointer=saver)
        logger.info("checkpointer ready: %d graphs compiled (%s)",
                    len(_graphs), ", ".join(_graphs.keys()))


def get_graph(task_type: str) -> Any:
    if not _graphs:
        raise RuntimeError(
            "Checkpointer 未就绪 —— setup_checkpointer() 未调用或 workflows 未导入。"
        )
    if task_type not in _graphs:
        raise KeyError(f"No graph registered for task_type: {task_type}")
    return _graphs[task_type]


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


async def run(task_type: str, initial_state: dict, thread_id: str) -> None:
    """Run workflow until done or interrupt. Publishes events to Redis pub/sub."""
    from agent.events import publish
    from redis_client import get_redis

    graph = get_graph(task_type)
    config = _config(thread_id)

    redis = await get_redis()
    await redis.set(f"agent:thread:{thread_id}:type", task_type, ex=86400)

    try:
        async for event in graph.astream(initial_state, config, stream_mode="updates"):
            if "__interrupt__" in event:
                state_snap = (await graph.aget_state(config)).values
                await publish(thread_id, {
                    "type": "waiting_review",
                    "draft": state_snap.get("draft"),
                    "task_type": task_type,
                })
                return
            node_name = next(iter(event))
            await publish(thread_id, {"type": "node_done", "node": node_name})

        final_snap = (await graph.aget_state(config)).values
        await publish(thread_id, {
            "type": "done",
            "final": final_snap.get("final"),
            "ir_action": final_snap.get("ir_action"),
        })
    except Exception as exc:
        logger.exception("agent runner failed thread=%s", thread_id)
        await publish(thread_id, {"type": "error", "message": str(exc)})


async def resume(task_type: str, thread_id: str, ir_decision: dict) -> None:
    """Resume a paused workflow with the IR's decision."""
    from agent.events import publish

    graph = get_graph(task_type)
    config = _config(thread_id)

    try:
        async for event in graph.astream(
            Command(resume=ir_decision), config, stream_mode="updates"
        ):
            if "__interrupt__" in event:
                state_snap = (await graph.aget_state(config)).values
                await publish(thread_id, {
                    "type": "waiting_review",
                    "draft": state_snap.get("draft"),
                    "task_type": task_type,
                })
                return
            node_name = next(iter(event))
            await publish(thread_id, {"type": "node_done", "node": node_name})

        final_snap = (await graph.aget_state(config)).values
        await publish(thread_id, {
            "type": "done",
            "final": final_snap.get("final"),
            "ir_action": final_snap.get("ir_action"),
        })
    except Exception as exc:
        logger.exception("agent runner failed thread=%s", thread_id)
        await publish(thread_id, {"type": "error", "message": str(exc)})
