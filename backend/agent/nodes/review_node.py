from langgraph.types import interrupt


def review_node(state: dict) -> dict:
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
