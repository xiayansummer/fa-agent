from __future__ import annotations
import httpx
from sqlalchemy import select
from langgraph.graph import StateGraph, START, END
from agent.state import AgentState
from agent.nodes.review_node import review_node
from agent.runner import _checkpointer, register_graph
from database import AsyncSessionLocal
from harness.skill_registry import skill_registry
from harness.prompt_registry import registry as prompt_registry
from models.investors import Investor
from models.interaction_logs import InteractionLog
from models.outreach_records import OutreachRecord
from models.agent_traces import AgentTrace


async def fetch_profiles_node(state: AgentState) -> dict:
    investor_ids = state.get("investor_ids") or []
    if not investor_ids:
        return {"investor_profiles": "（无关联投资人信息）"}
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Investor).where(Investor.id.in_(investor_ids)))
        investors = result.scalars().all()
    lines = []
    for inv in investors:
        lines.append(f"姓名：{inv.name}，机构：{inv.agency or ''}，职位：{inv.position or ''}，备注：{inv.profile_notes or ''}")
    return {"investor_profiles": "\n".join(lines) or "（无相关信息）"}


async def transcribe_node(state: AgentState) -> dict:
    """Use transcript directly if provided; otherwise call ASR skill."""
    if state.get("transcript"):
        return {}
    if not state.get("audio_url"):
        return {"transcript": "（无转录内容）", "skills_called": []}
    async with httpx.AsyncClient() as client:
        resp = await client.get(state["audio_url"])
        resp.raise_for_status()
        audio_bytes = resp.content
    text = await skill_registry.call("ASR.音频转文字", audio_bytes=audio_bytes)
    return {"transcript": text, "skills_called": ["ASR.音频转文字"]}


async def generate_node(state: AgentState) -> dict:
    context = prompt_registry.get(
        "meeting_minutes.generate",
        variables={
            "investor_profiles": state.get("investor_profiles") or "",
            "transcript": state.get("transcript") or "",
        },
    )
    draft = await skill_registry.call("Claude.生成内容", context=context)
    return {
        "draft": draft,
        "prompt_version": "v1",
        "skills_called": ["Claude.生成内容"],
    }


async def save_node(state: AgentState) -> dict:
    final_content = state.get("final") or ""
    investor_ids = state.get("investor_ids") or []
    async with AsyncSessionLocal() as db:
        if state.get("ir_action") != "rejected":
            for inv_id in investor_ids:
                db.add(InteractionLog(
                    investor_id=inv_id,
                    ir_id=state["ir_id"],
                    type="meeting",
                    summary=final_content[:500],
                    raw_content=state.get("transcript") or "",
                    agent_generated=True,
                ))
                db.add(OutreachRecord(
                    investor_id=inv_id,
                    ir_id=state["ir_id"],
                    type="meeting_minutes",
                    content=final_content,
                    status="approved" if state.get("ir_action") in ("approved", "modified") else "draft",
                ))
        db.add(AgentTrace(
            thread_id=state["thread_id"],
            ir_id=state["ir_id"],
            agent_name="meeting_minutes",
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
builder.add_node("fetch_profiles", fetch_profiles_node)
builder.add_node("transcribe", transcribe_node)
builder.add_node("generate", generate_node)
builder.add_node("review", review_node)
builder.add_node("save", save_node)

builder.add_edge(START, "fetch_profiles")
builder.add_edge("fetch_profiles", "transcribe")
builder.add_edge("transcribe", "generate")
builder.add_edge("generate", "review")
builder.add_edge("review", "save")
builder.add_edge("save", END)

meeting_minutes_graph = builder.compile(checkpointer=_checkpointer)
register_graph("meeting_minutes", meeting_minutes_graph)
