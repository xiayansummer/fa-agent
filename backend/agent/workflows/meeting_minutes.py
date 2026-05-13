from __future__ import annotations
import logging
import httpx
from sqlalchemy import select

logger = logging.getLogger(__name__)
from langgraph.graph import StateGraph, START, END
from agent.state import AgentState
from agent.nodes.review_node import review_node
from agent.nodes.fetch_tencent_minutes import fetch_tencent_minutes_node
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
    """Use transcript directly if provided; otherwise call ASR skill via URL.

    DashScope Qwen3-ASR-Flash 从公网拉取 audio_url（Qiniu 签名 URL 即可），
    不需要服务端先下载。
    """
    if state.get("transcript"):
        return {}
    if not state.get("audio_url"):
        return {"transcript": "（无转录内容）", "skills_called": []}
    text = await skill_registry.call("ASR.音频转文字", audio_url=state["audio_url"])
    return {"transcript": text, "skills_called": ["ASR.音频转文字"]}


async def generate_node(state: AgentState) -> dict:
    profiles = state.get("investor_profiles") or ""
    profiles_escaped = profiles.replace("{", "{{").replace("}", "}}")
    transcript = state.get("transcript") or ""
    transcript_escaped = transcript.replace("{", "{{").replace("}", "}}")
    context = prompt_registry.get(
        "meeting_minutes.generate",
        variables={
            "investor_profiles": profiles_escaped,
            "transcript": transcript_escaped,
        },
    )
    draft = await skill_registry.call("Claude.生成内容", context=context)
    return {
        "draft": draft,
        "prompt_version": "v1",
        "skills_called": ["Claude.生成内容"],
    }


async def summarize_for_interaction_node(state: AgentState) -> dict:
    """Content Agent：把完整纪要压缩成 80-120 字的互动摘要，写入 InteractionLog.summary。
    rejected 时跳过（不会落库），失败时降级用 final 的前 120 字。"""
    if state.get("ir_action") == "rejected":
        return {"interaction_summary": ""}
    final_content = (state.get("final") or "").strip()
    if not final_content:
        return {"interaction_summary": ""}
    if not (state.get("investor_ids") or []):
        # 没关联投资人就不写互动表，不浪费一次 LLM
        return {"interaction_summary": ""}
    try:
        escaped = final_content.replace("{", "{{").replace("}", "}}")
        context = prompt_registry.get(
            "meeting_minutes.interaction_summary",
            variables={"full_minutes": escaped},
        )
        short = await skill_registry.call("Claude.生成内容", context=context, max_tokens=256)
        short = (short or "").strip()
        if not short:
            short = final_content[:120]
    except Exception as e:
        logger.warning("interaction_summary failed: %s; fallback to truncation", e)
        short = final_content[:120]
    return {
        "interaction_summary": short,
        "skills_called": ["Claude.生成内容(互动摘要)"],
    }


async def save_node(state: AgentState) -> dict:
    from datetime import datetime
    from models.investors import Investor
    from sqlalchemy import select
    final_content = state.get("final") or ""
    short_summary = (state.get("interaction_summary") or "").strip() or final_content[:120]
    investor_ids = state.get("investor_ids") or []
    occurred_at = datetime.now()
    async with AsyncSessionLocal() as db:
        if state.get("ir_action") != "rejected" and investor_ids:
            inv_rows = (await db.execute(
                select(Investor).where(Investor.id.in_(investor_ids))
            )).scalars().all()
            inv_map = {inv.id: inv for inv in inv_rows}

            for inv_id in investor_ids:
                db.add(InteractionLog(
                    investor_id=inv_id,
                    ir_id=state["ir_id"],
                    type="meeting",
                    occurred_at=occurred_at,
                    summary=short_summary,            # Content Agent 二次提炼的短摘要
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
                # 推进 investor.last_interaction_at（仅当新事件更晚）
                inv = inv_map.get(inv_id)
                if inv and (not inv.last_interaction_at or occurred_at > inv.last_interaction_at):
                    inv.last_interaction_at = occurred_at
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
builder.add_node("fetch_tencent_minutes", fetch_tencent_minutes_node)
builder.add_node("transcribe", transcribe_node)
builder.add_node("generate", generate_node)
builder.add_node("review", review_node)
builder.add_node("summarize_for_interaction", summarize_for_interaction_node)
builder.add_node("save", save_node)

builder.add_edge(START, "fetch_profiles")
builder.add_edge("fetch_profiles", "fetch_tencent_minutes")
builder.add_edge("fetch_tencent_minutes", "transcribe")
builder.add_edge("transcribe", "generate")
builder.add_edge("generate", "review")
builder.add_edge("review", "summarize_for_interaction")
builder.add_edge("summarize_for_interaction", "save")
builder.add_edge("save", END)

meeting_minutes_graph = builder.compile(checkpointer=_checkpointer)
register_graph("meeting_minutes", meeting_minutes_graph)
