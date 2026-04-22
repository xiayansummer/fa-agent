from __future__ import annotations
from typing import Any
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

_checkpointer = MemorySaver()
_graphs: dict[str, Any] = {}


def register_graph(task_type: str, graph: Any) -> None:
    _graphs[task_type] = graph


def get_graph(task_type: str) -> Any:
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

    # Store task_type in Redis for resume routing
    redis = await get_redis()
    await redis.set(f"agent:thread:{thread_id}:type", task_type, ex=86400)

    try:
        async for event in graph.astream(initial_state, config, stream_mode="updates"):
            if "__interrupt__" in event:
                state_snap = graph.get_state(config).values
                await publish(thread_id, {
                    "type": "waiting_review",
                    "draft": state_snap.get("draft"),
                    "task_type": task_type,
                })
                return
            node_name = next(iter(event))
            await publish(thread_id, {"type": "node_done", "node": node_name})

        final_snap = graph.get_state(config).values
        await publish(thread_id, {
            "type": "done",
            "final": final_snap.get("final"),
            "ir_action": final_snap.get("ir_action"),
        })
    except Exception as exc:
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
                state_snap = graph.get_state(config).values
                await publish(thread_id, {
                    "type": "waiting_review",
                    "draft": state_snap.get("draft"),
                    "task_type": task_type,
                })
                return
            node_name = next(iter(event))
            await publish(thread_id, {"type": "node_done", "node": node_name})

        final_snap = graph.get_state(config).values
        await publish(thread_id, {
            "type": "done",
            "final": final_snap.get("final"),
            "ir_action": final_snap.get("ir_action"),
        })
    except Exception as exc:
        await publish(thread_id, {"type": "error", "message": str(exc)})
