"""Orchestrator workflow：IR 打开 chat 时聚合今日信号生成早安卡。

不走 review—— 简报是 informational，节点链路：
  START → fetch_signals → synthesize_briefing → END (done)

state.final 是结构化 JSON 字符串：
  {greeting, highlights[], suggested_actions[]}
"""
from __future__ import annotations
import json
import logging
from datetime import date, datetime, timedelta
from sqlalchemy import select, func
from langgraph.graph import StateGraph, START, END
from agent.state import AgentState
from agent.runner import _checkpointer, register_graph
from database import AsyncSessionLocal
from harness.skill_registry import skill_registry
from harness.prompt_registry import registry as prompt_registry
from models.investors import Investor
from models.outreach_records import OutreachRecord
from models.ir_users import IRUser
# 复用 calendar 端点已有的聚合逻辑
from api.calendar import _compute_events_for_day, _load_tencent_meetings

logger = logging.getLogger(__name__)


def _esc(s: str) -> str:
    """把字符串里的 {} 转义为 {{ }}，避免 prompt_registry 的 str.format 把它们当占位符。"""
    return s.replace("{", "{{").replace("}", "}}")


async def fetch_signals_node(state: AgentState) -> dict:
    ir_id = state["ir_id"]
    target = date.fromisoformat(state["target_date"]) if state.get("target_date") else date.today()
    target_str = str(target)

    async with AsyncSessionLocal() as db:
        # 1) 投资人 + 今日日历事件
        investors_result = await db.execute(select(Investor).where(Investor.is_active == True))
        investors = investors_result.scalars().all()
        events = _compute_events_for_day(investors, target)

        # 2) 腾讯会议（_load_tencent_meetings 内部有 5min Redis 缓存）
        meetings = await _load_tencent_meetings(db, ir_id)

        # 3) 待审草稿数
        pending_result = await db.execute(
            select(func.count()).select_from(OutreachRecord).where(
                OutreachRecord.ir_id == ir_id,
                OutreachRecord.status == "draft",
            )
        )
        pending = pending_result.scalar() or 0

        # 4) 最近 24h 新增投资人（团队新动向）
        cutoff = datetime.now() - timedelta(days=1)
        recent_result = await db.execute(
            select(Investor.name, Investor.agency).where(
                Investor.is_active == True,
                Investor.created_at >= cutoff,
            ).limit(5)
        )
        recent_rows = recent_result.all()

        # 5) IR 自己的姓名
        ir_row_result = await db.execute(select(IRUser).where(IRUser.id == ir_id))
        ir_row = ir_row_result.scalar_one_or_none()
        ir_name = ir_row.name if ir_row else "IR"

    today_meetings = [m for m in meetings if m["date"] == target_str]

    calendar_str = "\n".join(
        f"- {e.time} {e.type} | {e.title}" + (f" (investor_id={e.investor_id})" if e.investor_id else "")
        for e in events
    ) or "（今日无日历事件）"

    meetings_str = "\n".join(
        f"- {m['time']}-{m['end_time']} 「{m['subject']}」 (tencent_meeting_id={m['meeting_id']})"
        for m in today_meetings
    ) or "（今日无腾讯会议）"

    recent_str = "\n".join(
        f"- {r.name}（{r.agency or '未填机构'}）" for r in recent_rows
    ) or "（无）"

    signals = {
        "ir_name": ir_name,
        "today": target_str,
        "calendar_events": calendar_str,
        "tencent_meetings": meetings_str,
        "pending_count": pending,
        "recent_changes": recent_str,
    }
    return {
        "briefing_signals": json.dumps(signals, ensure_ascii=False),
        "skills_called": ["日历.聚合", "腾讯会议.list"],
    }


async def synthesize_briefing_node(state: AgentState) -> dict:
    sig = json.loads(state.get("briefing_signals") or "{}")
    context = prompt_registry.get(
        "orchestrator.briefing",
        variables={
            "ir_name": sig.get("ir_name", "IR"),
            "today": sig.get("today", ""),
            "calendar_events": _esc(sig.get("calendar_events", "")),
            "tencent_meetings": _esc(sig.get("tencent_meetings", "")),
            "pending_count": str(sig.get("pending_count", 0)),
            "recent_changes": _esc(sig.get("recent_changes", "")),
        },
    )
    try:
        draft = await skill_registry.call("Claude.生成内容", context=context, max_tokens=800)
    except Exception as e:
        logger.warning("orchestrator briefing Claude call failed: %s", e)
        # 降级：用本地拼接，不让前端拿空
        fallback = {
            "greeting": f"早上好 {sig.get('ir_name', '')}，今日有 {sig.get('pending_count', 0)} 个待审草稿",
            "highlights": [],
            "suggested_actions": [
                {"label": "查看日程", "task_type": "navigate", "target": "calendar"},
            ],
        }
        return {
            "draft": "",
            "final": json.dumps(fallback, ensure_ascii=False),
            "ir_action": "approved",
            "prompt_version": "v1",
            "skills_called": ["Claude.生成内容(失败降级)"],
        }

    # 校验 Claude 输出是合法 JSON；不是则降级
    try:
        parsed = json.loads(draft.strip())
        if not isinstance(parsed, dict) or "greeting" not in parsed:
            raise ValueError("missing greeting")
        # 规范化字段
        parsed.setdefault("highlights", [])
        parsed.setdefault("suggested_actions", [])
        final = json.dumps(parsed, ensure_ascii=False)
    except Exception as e:
        logger.warning("orchestrator briefing JSON parse failed: %s. raw=%s", e, draft[:300])
        final = json.dumps({
            "greeting": draft.strip().splitlines()[0][:80] if draft else "今日无 Agent 安排",
            "highlights": [],
            "suggested_actions": [],
        }, ensure_ascii=False)

    return {
        "draft": draft,
        "final": final,
        "ir_action": "approved",
        "prompt_version": "v1",
        "skills_called": ["Claude.生成内容"],
    }


builder = StateGraph(AgentState)
builder.add_node("fetch_signals", fetch_signals_node)
builder.add_node("synthesize_briefing", synthesize_briefing_node)

builder.add_edge(START, "fetch_signals")
builder.add_edge("fetch_signals", "synthesize_briefing")
builder.add_edge("synthesize_briefing", END)

orchestrator_graph = builder.compile(checkpointer=_checkpointer)
register_graph("briefing", orchestrator_graph)
