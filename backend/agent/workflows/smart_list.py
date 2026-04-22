from __future__ import annotations
import json
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


async def fetch_candidates_node(state: AgentState) -> dict:
    async with AsyncSessionLocal() as db:
        stmt = select(Investor).where(Investor.is_active == True)
        if state.get("candidate_ids"):
            stmt = stmt.where(Investor.id.in_(state["candidate_ids"]))
        result = await db.execute(stmt)
        investors = result.scalars().all()
    lines = []
    for inv in investors:
        lines.append(
            f"[ID:{inv.id}] 姓名：{inv.name}，机构：{inv.agency or ''}，"
            f"行业偏好：{json.dumps(inv.industry_tags or [], ensure_ascii=False)}，"
            f"阶段偏好：{json.dumps(inv.stage_pref or [], ensure_ascii=False)}，"
            f"投资规模：{inv.quota_range or '未知'}，"
            f"备注：{(inv.profile_notes or '')[:200]}"
        )
    return {
        "investor_profiles": "\n".join(lines) or "（无候选投资人）",
        "candidate_ids": [inv.id for inv in investors],
    }


async def rank_node(state: AgentState) -> dict:
    criteria_escaped = (state.get("criteria") or "").replace("{", "{{").replace("}", "}}")
    profiles_escaped = (state.get("investor_profiles") or "").replace("{", "{{").replace("}", "}}")
    context = prompt_registry.get(
        "smart_list.rank",
        variables={
            "criteria": criteria_escaped,
            "investor_profiles": profiles_escaped,
        },
    )
    ranked_json = await skill_registry.call("Claude.生成内容", context=context)
    return {"draft": ranked_json, "prompt_version": "v1", "skills_called": ["Claude.生成内容"]}


async def format_list_node(state: AgentState) -> dict:
    """Parse ranked JSON and format as human-readable draft for IR review."""
    try:
        items = json.loads(state.get("draft") or "[]")
    except (json.JSONDecodeError, TypeError):
        logger.warning("smart_list format_list_node: failed to parse draft as JSON, thread_id=%s", state.get("thread_id"))
        return {}
    lines = ["智能推荐投资人名单：\n"]
    for i, item in enumerate(items, 1):
        lines.append(
            f"{i}. [ID:{item.get('investor_id', '?')}] "
            f"优先级：{item.get('priority', '中')}  "
            f"匹配分：{item.get('score', 0)}\n"
            f"   推荐理由：{item.get('reason', '')}\n"
        )
    return {"draft": "\n".join(lines)}


async def save_node(state: AgentState) -> dict:
    final_content = state.get("final") or ""
    async with AsyncSessionLocal() as db:
        if state.get("ir_action") != "rejected":
            try:
                items = json.loads(final_content)
                investor_ids_in_list = [item["investor_id"] for item in items]
            except (json.JSONDecodeError, TypeError, KeyError):
                logger.warning("smart_list save_node: failed to parse final as JSON, thread_id=%s", state.get("thread_id"))
                investor_ids_in_list = state.get("candidate_ids") or []
            for inv_id in investor_ids_in_list:
                db.add(OutreachRecord(
                    investor_id=inv_id,
                    ir_id=state["ir_id"],
                    type="industry_report",
                    content=final_content,
                    status="approved" if state.get("ir_action") in ("approved", "modified") else "draft",
                ))
        db.add(AgentTrace(
            thread_id=state["thread_id"],
            ir_id=state["ir_id"],
            agent_name="smart_list",
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
builder.add_node("fetch_candidates", fetch_candidates_node)
builder.add_node("rank", rank_node)
builder.add_node("format_list", format_list_node)
builder.add_node("review", review_node)
builder.add_node("save", save_node)

builder.add_edge(START, "fetch_candidates")
builder.add_edge("fetch_candidates", "rank")
builder.add_edge("rank", "format_list")
builder.add_edge("format_list", "review")
builder.add_edge("review", "save")
builder.add_edge("save", END)

smart_list_graph = builder.compile(checkpointer=_checkpointer)
register_graph("smart_list", smart_list_graph)
