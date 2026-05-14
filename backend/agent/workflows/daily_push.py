from __future__ import annotations
import json
import logging
from datetime import date, timedelta
from sqlalchemy import select
from langgraph.graph import StateGraph, START, END
from agent.state import AgentState
from agent.nodes.review_node import review_node
from agent.runner import register_builder
from database import AsyncSessionLocal
from harness.skill_registry import skill_registry
from harness.prompt_registry import registry as prompt_registry
from models.investors import Investor
from models.outreach_records import OutreachRecord
from models.agent_traces import AgentTrace

logger = logging.getLogger(__name__)


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


_EMOJIS = ["💬", "✨", "📨", "🌟", "🤝", "💡"]


def _render_messages(raw: str, events: list[dict]) -> str:
    """把 LLM 输出的 JSON 数组渲染成人类可读 markdown。
    单条：直接显示 message 文本（前置 1 个 emoji）
    多条：每段一个 emoji + 投资人名 + message
    解析失败：原样返回 raw（save_node 仍能 fallback）
    """
    try:
        items = json.loads(raw)
        if not isinstance(items, list) or not items:
            return raw
    except (json.JSONDecodeError, TypeError):
        return raw

    # 投资人 id → 姓名（来自 events 上下文）
    name_by_id: dict[int, str] = {e["investor_id"]: e.get("name", "") for e in (events or [])}

    if len(items) == 1:
        msg = (items[0].get("message") or "").strip()
        return f"{_EMOJIS[0]} {msg}" if msg else raw

    parts: list[str] = []
    for i, item in enumerate(items):
        emoji = _EMOJIS[i % len(_EMOJIS)]
        inv_id = item.get("investor_id")
        nm = name_by_id.get(inv_id, "")
        msg = (item.get("message") or "").strip()
        head = f"{emoji} 给 {nm} 的跟进" if nm else f"{emoji}"
        parts.append(f"{head}\n\n{msg}")
    return "\n\n---\n\n".join(parts)


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
    raw = await skill_registry.call("Claude.生成内容", context=context)
    draft = _render_messages(raw, state.get("events") or [])
    return {
        "draft": draft,
        "generated_messages_json": raw,   # 留给 save_node 按 investor_id 分发
        "prompt_version": "v1",
        "skills_called": ["Claude.生成内容"],
    }


async def save_node(state: AgentState) -> dict:
    final_content = state.get("final") or ""
    events = state.get("events") or []
    async with AsyncSessionLocal() as db:
        if state.get("ir_action") != "rejected":
            # 优先用 review 前 LLM 输出的 raw JSON（保留 per-investor 分发能力），
            # 用户编辑过 final 时 fallback 到把 final 当统一文案发给所有投资人。
            messages = None
            raw_json = state.get("generated_messages_json") or ""
            if state.get("ir_action") == "approved" and raw_json:
                try:
                    parsed = json.loads(raw_json)
                    if isinstance(parsed, list) and parsed:
                        messages = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            if messages is None:
                try:
                    parsed = json.loads(final_content)
                    if isinstance(parsed, list) and parsed:
                        messages = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            if messages is None:
                # 用户编辑过 final（markdown 文本），统一发给所有 events 投资人
                logger.info("daily_push save_node: using user-edited final as unified content. thread_id=%s", state.get("thread_id"))
                messages = [
                    {"investor_id": e["investor_id"], "message": final_content}
                    for e in events
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

register_builder("daily_push", builder)

from langgraph.checkpoint.memory import MemorySaver as _MemorySaver
daily_push_graph = builder.compile(checkpointer=_MemorySaver())
