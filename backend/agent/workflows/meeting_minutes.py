from __future__ import annotations
import logging
import httpx
from sqlalchemy import select

logger = logging.getLogger(__name__)
from langgraph.graph import StateGraph, START, END
from agent.state import AgentState
from agent.nodes.review_node import review_node
from agent.nodes.fetch_tencent_minutes import fetch_tencent_minutes_node
from agent.runner import register_builder
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


async def extract_action_items_node(state: AgentState) -> dict:
    """Content Agent：从纪要里解析 action items + 推断 due_date。"""
    if state.get("ir_action") == "rejected":
        return {"action_items": []}
    final_content = (state.get("final") or "").strip()
    if not final_content:
        return {"action_items": []}
    from datetime import date
    today_iso = date.today().isoformat()
    try:
        escaped = final_content.replace("{", "{{").replace("}", "}}")
        context = prompt_registry.get(
            "meeting_minutes.action_items",
            variables={"full_minutes": escaped, "today_iso": today_iso},
        )
        raw = await skill_registry.call("Claude.生成内容", context=context, max_tokens=600)
        import json as _json
        parsed = _json.loads((raw or "").strip())
        if not isinstance(parsed, list):
            parsed = []
        # 限 5 条 + 字段校验
        items = []
        for it in parsed[:5]:
            if not isinstance(it, dict):
                continue
            title = (it.get("title") or "").strip()
            t = (it.get("type") or "other").strip()
            due = (it.get("due_date") or "").strip()
            actor = (it.get("actor") or "").strip()
            if not actor:
                # 兜底：type 推断 actor
                actor = "investor" if t == "investor_milestone" else "ir"
            if title and due:
                items.append({"title": title, "actor": actor, "type": t, "due_date": due})
        return {
            "action_items": items,
            "skills_called": ["Claude.生成内容(action_items)"],
        }
    except Exception as e:
        logger.warning("extract_action_items failed: %s", e)
        return {"action_items": []}


async def dispatch_outreach_node(state: AgentState) -> dict:
    """Outreach Agent：对 meeting_request 和 investor_milestone 两类 action 生成
    草稿写 OutreachRecord(draft)。前者是预约线下，后者是提前 ping 投资人 milestone。"""
    if state.get("ir_action") == "rejected":
        return {}
    items = state.get("action_items") or []
    if not items:
        return {}
    investor_ids = state.get("investor_ids") or []
    if not investor_ids:
        return {}
    # 两类各起草
    DISPATCH_RULES = [
        ("meeting_request",     "outreach_agent.meeting_request",     "outreach_预约"),
        ("investor_milestone",  "outreach_agent.investor_milestone",  "outreach_milestone提醒"),
    ]
    target_items = [it for it in items if it.get("type") in {r[0] for r in DISPATCH_RULES}]
    if not target_items:
        return {}

    summary = (state.get("interaction_summary") or "").strip()
    if not summary:
        summary = (state.get("final") or "")[:200]

    async with AsyncSessionLocal() as db:
        inv_rows = (await db.execute(
            select(Investor).where(Investor.id.in_(investor_ids))
        )).scalars().all()
        inv_map = {inv.id: inv for inv in inv_rows}

        drafts_by_kind: dict[str, int] = {}
        for inv_id in investor_ids:
            inv = inv_map.get(inv_id)
            if not inv:
                continue
            for action in target_items:
                action_type = action.get("type")
                rule = next((r for r in DISPATCH_RULES if r[0] == action_type), None)
                if rule is None:
                    continue
                _, prompt_key, kind_label = rule
                # 不同 prompt key 占位变量名不一样（meeting_request 用 action_title，
                # investor_milestone 用 milestone_title）—— 用 dict union 双写兼容
                variables = {
                    "investor_name": inv.name,
                    "investor_agency": (inv.agency or "").replace("{", "{{").replace("}", "}}"),
                    "minutes_summary": summary.replace("{", "{{").replace("}", "}}"),
                    "action_title": action["title"].replace("{", "{{").replace("}", "}}"),
                    "milestone_title": action["title"].replace("{", "{{").replace("}", "}}"),
                    "due_date": action["due_date"],
                }
                try:
                    context = prompt_registry.get(prompt_key, variables=variables)
                    msg = await skill_registry.call("Claude.生成内容", context=context, max_tokens=400)
                    msg = (msg or "").strip()
                    if not msg:
                        continue
                    db.add(OutreachRecord(
                        investor_id=inv_id,
                        ir_id=state["ir_id"],
                        type="milestone_message",  # 复用现有枚举
                        content=msg,
                        status="draft",
                    ))
                    drafts_by_kind[kind_label] = drafts_by_kind.get(kind_label, 0) + 1
                except Exception as e:
                    logger.warning("outreach %s draft failed: %s", action_type, e)
        await db.commit()
    if drafts_by_kind:
        return {"skills_called": [f"Claude.生成内容({k}×{n})" for k, n in drafts_by_kind.items()]}
    return {}


async def save_node(state: AgentState) -> dict:
    from datetime import datetime, date as _date
    from models.investors import Investor
    from sqlalchemy import select
    final_content = state.get("final") or ""
    short_summary = (state.get("interaction_summary") or "").strip() or final_content[:120]
    investor_ids = state.get("investor_ids") or []
    occurred_at = datetime.now()
    # 取 action_items 里最早的 due_date 作为 next_followup_at
    next_followup_dt = None
    for ai in (state.get("action_items") or []):
        try:
            d = _date.fromisoformat(ai["due_date"])
            dt = datetime.combine(d, datetime.min.time())
            if next_followup_dt is None or dt < next_followup_dt:
                next_followup_dt = dt
        except Exception:
            continue
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
                    next_followup_at=next_followup_dt,  # 最早 action item 的 due_date
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
builder.add_node("extract_action_items", extract_action_items_node)
builder.add_node("save", save_node)
builder.add_node("dispatch_outreach", dispatch_outreach_node)

builder.add_edge(START, "fetch_profiles")
builder.add_edge("fetch_profiles", "fetch_tencent_minutes")
builder.add_edge("fetch_tencent_minutes", "transcribe")
builder.add_edge("transcribe", "generate")
builder.add_edge("generate", "review")
builder.add_edge("review", "summarize_for_interaction")
builder.add_edge("summarize_for_interaction", "extract_action_items")
builder.add_edge("extract_action_items", "save")
builder.add_edge("save", "dispatch_outreach")
builder.add_edge("save", END)

register_builder("meeting_minutes", builder)

from langgraph.checkpoint.memory import MemorySaver as _MemorySaver
meeting_minutes_graph = builder.compile(checkpointer=_MemorySaver())
