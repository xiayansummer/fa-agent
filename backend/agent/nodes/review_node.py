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
    if action == "rejected":
        final = None
    else:
        # 注意：HTTP 层 review.final 默认空字符串而非 None，所以这里要做空串兜底，
        # 否则 dict.get 拿到 "" 不会触发 fallback，save_node 落库 content 为空。
        provided = (ir_decision.get("final") or "").strip()
        final = provided or state.get("draft")
    out: dict = {"ir_action": action, "final": final}
    # IR 在审核时补传的投资人关联 → 覆盖 state.investor_ids，
    # 让后续 save_node / dispatch_outreach 拿到真投资人列表
    if ir_decision.get("investor_ids"):
        out["investor_ids"] = list(ir_decision["investor_ids"])
    return out
