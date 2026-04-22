from __future__ import annotations
import logging
from sqlalchemy import select
from langgraph.graph import StateGraph, START, END
from agent.state import AgentState
from agent.nodes.review_node import review_node
from agent.runner import _checkpointer, register_graph
from database import AsyncSessionLocal
from harness.skill_registry import skill_registry
from harness.prompt_registry import registry as prompt_registry
from models.investors import Investor
from models.outreach_records import OutreachRecord
from models.agent_traces import AgentTrace

logger = logging.getLogger(__name__)

_MILESTONE_LABELS = {
    "birthday": "生日",
    "join_agency": "入职纪念日",
    "first_meeting": "首次见面纪念日",
}


async def fetch_investor_node(state: AgentState) -> dict:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Investor).where(Investor.id == state["investor_id"]))
        inv = result.scalar_one_or_none()
    if not inv:
        return {"error": f"投资人 {state['investor_id']} 不存在"}
    profile = (
        f"姓名：{inv.name}，机构：{inv.agency or ''}，职位：{inv.position or ''}，"
        f"备注：{(inv.profile_notes or '')[:300]}"
    )
    return {"investor_profiles": profile}


async def generate_node(state: AgentState) -> dict:
    if state.get("error"):
        return {}
    milestone_label = _MILESTONE_LABELS.get(
        state.get("milestone_type") or "", state.get("milestone_type") or ""
    )
    profile_escaped = (state.get("investor_profiles") or "").replace("{", "{{").replace("}", "}}")
    context = prompt_registry.get(
        "milestone_message.generate",
        variables={
            "investor_profile": profile_escaped,
            "milestone_type": milestone_label,
            "ir_name": state.get("ir_name") or "IR",
        },
    )
    message = await skill_registry.call("Claude.生成内容", context=context, max_tokens=256)
    return {"draft": message, "prompt_version": "v1", "skills_called": ["Claude.生成内容"]}


async def save_node(state: AgentState) -> dict:
    if not state.get("investor_id"):
        return {}
    async with AsyncSessionLocal() as db:
        if state.get("ir_action") != "rejected":
            db.add(OutreachRecord(
                investor_id=state["investor_id"],
                ir_id=state["ir_id"],
                type="milestone_message",
                content=state.get("final") or "",
                status="approved" if state.get("ir_action") in ("approved", "modified") else "draft",
            ))
        db.add(AgentTrace(
            thread_id=state["thread_id"],
            ir_id=state["ir_id"],
            agent_name="milestone_outreach",
            prompt_version=state.get("prompt_version") or "v1",
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            skills_called=state.get("skills_called") or [],
            status="success",
        ))
        await db.commit()
    return {}


builder = StateGraph(AgentState)
builder.add_node("fetch_investor", fetch_investor_node)
builder.add_node("generate", generate_node)
builder.add_node("review", review_node)
builder.add_node("save", save_node)

builder.add_edge(START, "fetch_investor")
builder.add_edge("fetch_investor", "generate")
builder.add_edge("generate", "review")
builder.add_edge("review", "save")
builder.add_edge("save", END)

milestone_outreach_graph = builder.compile(checkpointer=_checkpointer)
register_graph("milestone_outreach", milestone_outreach_graph)
