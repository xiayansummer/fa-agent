from __future__ import annotations
import json
from datetime import date, timedelta
from sqlalchemy import select, or_
from sqlalchemy.sql import func as sqlfunc
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


async def fetch_events_node(state: AgentState) -> dict:
    target = date.fromisoformat(state["target_date"]) if state.get("target_date") else date.today()
    cutoff = target - timedelta(days=14)
    async with AsyncSessionLocal() as db:
        stmt = select(Investor).where(Investor.is_active == True)
        if state.get("investor_ids"):
            stmt = stmt.where(Investor.id.in_(state["investor_ids"]))
        result = await db.execute(stmt)
        investors = result.scalars().all()
    events = []
    for inv in investors:
        ev_types = []
        if inv.birthday and inv.birthday.month == target.month and inv.birthday.day == target.day:
            ev_types.append("生日")
        if inv.join_agency_date and inv.join_agency_date.month == target.month and inv.join_agency_date.day == target.day:
            ev_types.append("入职纪念日")
        no_recent = (inv.last_interaction_at is None) or (inv.last_interaction_at.date() < cutoff)
        if not ev_types and no_recent:
            ev_types.append("常规跟进")
        if ev_types:
            events.append({"investor_id": inv.id, "name": inv.name, "agency": inv.agency or "", "event_types": ev_types})
    return {"events": events}


async def fetch_profiles_node(state: AgentState) -> dict:
    events = state.get("events") or []
    investor_ids = [e["investor_id"] for e in events]
    if not investor_ids:
        return {"investor_profiles": "（无关联投资人）"}
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Investor).where(Investor.id.in_(investor_ids)))
        investors = result.scalars().all()
    inv_map = {inv.id: inv for inv in investors}
    lines = []
    for ev in events:
        inv = inv_map.get(ev["investor_id"])
        if inv:
            lines.append(
                f"[ID:{inv.id}] 姓名：{inv.name}，机构：{inv.agency or ''}，"
                f"关怀事件：{'、'.join(ev['event_types'])}，备注：{(inv.profile_notes or '')[:200]}"
            )
    return {"investor_profiles": "\n".join(lines) or "（无相关信息）"}


async def generate_node(state: AgentState) -> dict:
    events_str = json.dumps(state.get("events") or [], ensure_ascii=False, indent=2)
    events_str_escaped = events_str.replace("{", "{{").replace("}", "}}")
    profiles = state.get("investor_profiles") or ""
    profiles_escaped = profiles.replace("{", "{{").replace("}", "}}")
    context = prompt_registry.get(
        "daily_push.generate",
        variables={
            "events": events_str_escaped,
            "investor_profiles": profiles_escaped,
        },
    )
    draft = await skill_registry.call("Claude.生成内容", context=context)
    return {"draft": draft, "prompt_version": "v1", "skills_called": ["Claude.生成内容"]}


async def save_node(state: AgentState) -> dict:
    final_content = state.get("final") or ""
    async with AsyncSessionLocal() as db:
        if state.get("ir_action") != "rejected":
            try:
                messages = json.loads(final_content)
            except (json.JSONDecodeError, TypeError):
                messages = [
                    {"investor_id": e["investor_id"], "message": final_content}
                    for e in (state.get("events") or [])
                ]
            for item in messages:
                db.add(OutreachRecord(
                    investor_id=item["investor_id"],
                    ir_id=state["ir_id"],
                    type="daily_push",
                    content=item.get("message", ""),
                    status="approved" if state.get("ir_action") in ("approved", "modified") else "draft",
                ))
        db.add(AgentTrace(
            thread_id=state["thread_id"],
            ir_id=state["ir_id"],
            agent_name="daily_push",
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
builder.add_node("fetch_events", fetch_events_node)
builder.add_node("fetch_profiles", fetch_profiles_node)
builder.add_node("generate", generate_node)
builder.add_node("review", review_node)
builder.add_node("save", save_node)

builder.add_edge(START, "fetch_events")
builder.add_edge("fetch_events", "fetch_profiles")
builder.add_edge("fetch_profiles", "generate")
builder.add_edge("generate", "review")
builder.add_edge("review", "save")
builder.add_edge("save", END)

daily_push_graph = builder.compile(checkpointer=_checkpointer)
register_graph("daily_push", daily_push_graph)
