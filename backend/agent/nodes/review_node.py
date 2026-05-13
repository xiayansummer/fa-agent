from langgraph.types import interrupt
from agent.state import AgentState


async def review_node(state: AgentState) -> dict:
    """Pause workflow for IR review.

    Must be async so LangGraph keeps the runnable contextvars on the same
    coroutine; a sync node gets dispatched to a worker thread under
    Python 3.9, which drops the context and causes interrupt() to raise
    "Called get_config outside of a runnable context".
    """
    ir_decision = interrupt({
        "draft": state.get("draft"),
        "task_type": state.get("task_type"),
    })
    action = ir_decision["action"]
    final = ir_decision.get("final", state.get("draft")) if action != "rejected" else None
    return {
        "ir_action": action,
        "final": final,
    }
