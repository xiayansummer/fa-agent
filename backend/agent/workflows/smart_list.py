from __future__ import annotations
import asyncio
import json
import logging
import re
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
from skills.qmingpian import qmingpian_export_ongoing_lunci, qmingpian_export_agency

logger = logging.getLogger(__name__)


def _strip_code_fence(raw: str) -> str:
    """剥掉 LLM 可能加的 ```json ... ``` 围栏，提取最外层 JSON 数组。
    minimax 等模型常把 JSON 包在 markdown 代码块里，否则 json.loads 失败、
    草稿直接显示原始 JSON 给 IR。"""
    t = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(\[.*\])\s*```", t, re.DOTALL)
    if m:
        return m.group(1)
    if not t.startswith("["):
        m2 = re.search(r"\[.*\]", t, re.DOTALL)
        if m2:
            return m2.group(0)
    return t


def _esc(s: str) -> str:
    """prompt_registry 用 str.format 渲染，候选文本里的花括号要转义。"""
    return (s or "").replace("{", "{{").replace("}", "}}")


def _route_entry(state: AgentState) -> str:
    """带 candidate_ids（从日历/投资人详情指定人进来）→ 旧·投资人重排；
    否则（自由出名单）→ 方向A·机构名单（企名片活跃池 + 全公司共享库）。"""
    return "investor" if state.get("candidate_ids") else "agency"


# ============ 旧分支：指定投资人重排（candidate_ids 入口，保留兼容） ============

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
    context = prompt_registry.get(
        "smart_list.rank",
        variables={
            "criteria": _esc(state.get("criteria") or ""),
            "investor_profiles": _esc(state.get("investor_profiles") or ""),
        },
    )
    ranked_json = await skill_registry.call("Claude.生成内容", context=context)
    return {"draft": ranked_json, "prompt_version": "v1", "skills_called": ["Claude.生成内容"]}


async def format_list_node(state: AgentState) -> dict:
    """Parse ranked JSON and format as human-readable draft for IR review."""
    try:
        items = json.loads(_strip_code_fence(state.get("draft") or "[]"))
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


# ============ 方向A：机构名单（自由 criteria 入口） ============

_MAX_POOL = 300          # 候选池上限（ongoing 全量 + 本地共享库）
_MAX_SHORTLIST = 15      # LLM 粗筛后进入证据拉取的机构数
_ENRICH_CONCURRENCY = 5  # export_agency 并发
_ENRICH_TIMEOUT = 12.0   # 单家机构证据拉取超时（秒）
# 粗筛/精排都是长输入任务：m2.7 长输入 40% 空响应、重试叠加会顶爆 skill 90s 超时
# （2026-06-11 实测 shortlist 节点 TimeoutError），改用长文本稳定的 qwen3.7-plus。
_LONG_TEXT_MODEL = "qwen3.7-plus"


async def discover_agencies_node(state: AgentState) -> dict:
    """候选机构发现：企名片 ongoing 活跃对接池 ∪ 本地共享投资人库的机构。
    注意：投资人库按产品决策是全公司共享池（2026-06-11），这里不按 IR 过滤。"""
    names: list[str] = []
    skills_called = []
    try:
        d = await asyncio.wait_for(qmingpian_export_ongoing_lunci(""), timeout=20)
        names += [
            (c.get("agency") or "").strip()
            for c in (d.get("contacts") or [])
            if (c.get("agency") or "").strip()
        ]
        skills_called.append("企名片.导出ongoing项目对接")
    except Exception as e:
        logger.warning("smart_list discover: ongoing pool failed, fallback to local only: %s", e)

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Investor.agency).where(Investor.is_active == True)
        )).scalars().all()
    names += [(a or "").strip() for a in rows if (a or "").strip()]

    seen: dict[str, None] = {}
    for n in names:
        if n not in seen:
            seen[n] = None
    pool = list(seen.keys())[:_MAX_POOL]
    if not pool:
        raise RuntimeError("候选机构池为空：企名片 ongoing 拉取失败且本地投资人库无机构信息")
    return {"agency_candidates": pool, "skills_called": skills_called}


async def shortlist_node(state: AgentState) -> dict:
    """LLM 按 criteria 在名字层面粗筛 ≤15 家，避免对全池逐家拉证据。
    LLM 失败（超时/空响应/输出不可解析）一律降级取池前 N 家——粗筛是优化不是闸门，
    不允许它卡死整个工作流。"""
    pool: list[str] = state.get("agency_candidates") or []
    picked: list[str] = []
    try:
        context = prompt_registry.get(
            "smart_list.shortlist",
            variables={
                "criteria": _esc(state.get("criteria") or ""),
                "agency_names": _esc("\n".join(pool)),
            },
        )
        raw = await skill_registry.call(
            "Claude.生成内容", context=context, model=_LONG_TEXT_MODEL,
        )
        pool_set = set(pool)
        arr = json.loads(_strip_code_fence(raw))
        for x in arr:
            if isinstance(x, str) and x.strip() in pool_set and x.strip() not in picked:
                picked.append(x.strip())
    except Exception as e:
        logger.warning("smart_list shortlist: LLM 粗筛失败（%s），降级取池前 %d 家", e, _MAX_SHORTLIST)
    if not picked:
        picked = pool[:_MAX_SHORTLIST]
    return {"agency_candidates": picked[:_MAX_SHORTLIST], "skills_called": ["Claude.生成内容"]}


def _evidence_block(name: str, d: dict) -> str:
    tags = "、".join((d.get("tags") or [])[:12]) or "（无标签）"
    sums = (d.get("summaries") or [])[-3:]   # 导出按时间升序，取最近 3 条
    hist = (d.get("history") or [])[:5]      # 导出最近在前，取 5 条
    lines = [f"### {name}", f"标签：{tags}"]
    if sums:
        lines.append("内部纪要（最近）：")
        for s in sums:
            content = (s.get("content") or "").replace("\n", " ")[:150]
            lines.append(f"- [{(s.get('created_at') or '')[:10]}] {content}")
    if hist:
        lines.append("历史推荐记录：")
        for h in hist:
            fb = (h.get("feedback") or "").replace("\n", " ")[:40]
            lines.append(
                f"- {h.get('event', '')} 状态:{h.get('status', '')}"
                + (f" 反馈:{fb}" if fb else "")
            )
    if not sums and not hist:
        lines.append("（企名片暂无纪要/推荐记录）")
    return "\n".join(lines)


async def enrich_agencies_node(state: AgentState) -> dict:
    """对粗筛后的机构并发拉企名片证据（标签 + 纪要 + 历史推荐）。失败的跳过不阻塞。"""
    picked: list[str] = state.get("agency_candidates") or []
    sem = asyncio.Semaphore(_ENRICH_CONCURRENCY)

    async def one(name: str) -> str:
        async with sem:
            try:
                d = await asyncio.wait_for(qmingpian_export_agency(name), timeout=_ENRICH_TIMEOUT)
                return _evidence_block(name, d)
            except Exception as e:
                logger.info("smart_list enrich: %s 拉取失败（%s），降级仅凭名字", name, e)
                return f"### {name}\n（企名片暂无详情，仅凭机构名与公开认知评估）"

    blocks = await asyncio.gather(*(one(n) for n in picked))
    return {
        "agency_evidence": "\n\n".join(blocks),
        "skills_called": ["企名片.导出机构详情"],
    }


async def agency_rank_node(state: AgentState) -> dict:
    context = prompt_registry.get(
        "smart_list.agency_rank",
        variables={
            "criteria": _esc(state.get("criteria") or ""),
            "agency_evidence": _esc(state.get("agency_evidence") or ""),
        },
    )
    ranked_json = await skill_registry.call(
        "Claude.生成内容", context=context, max_tokens=3072, model=_LONG_TEXT_MODEL,
    )
    return {"draft": ranked_json, "prompt_version": "agency-v1", "skills_called": ["Claude.生成内容"]}


async def format_agency_list_node(state: AgentState) -> dict:
    """机构名单渲染 + 本所联系人标注（共享投资人池按机构名双向模糊匹配）。"""
    try:
        items = json.loads(_strip_code_fence(state.get("draft") or "[]"))
    except (json.JSONDecodeError, TypeError):
        logger.warning("smart_list format_agency: draft 非 JSON, thread_id=%s", state.get("thread_id"))
        return {}

    async with AsyncSessionLocal() as db:
        invs = (await db.execute(
            select(Investor).where(Investor.is_active == True)
        )).scalars().all()

    def contacts_for(agency: str) -> list[str]:
        out = []
        a = (agency or "").strip()
        if len(a) < 2:
            return out
        for inv in invs:
            ia = (inv.agency or "").strip()
            if len(ia) >= 2 and (a in ia or ia in a):
                fam = f"，熟悉度{inv.familiarity}" if getattr(inv, "familiarity", None) else ""
                out.append(f"{inv.name}（{ia}{fam}）")
        return out[:5]

    lines = ["智能推荐机构名单（候选池：企名片活跃对接 + 全公司投资人库）：\n"]
    for i, item in enumerate(items, 1):
        agency = str(item.get("agency") or "?")
        contacts = contacts_for(agency)
        contact_line = "本所联系人：" + ("、".join(contacts) if contacts else "暂无（需新开拓）")
        lines.append(
            f"{i}. {agency}  优先级：{item.get('priority', '中')}  匹配分：{item.get('score', 0)}\n"
            f"   推荐理由：{item.get('reason', '')}\n"
            f"   {contact_line}\n"
        )
    if len(items) == 0:
        lines.append("（按当前需求没有 ≥50 分的机构，建议放宽条件或补充项目信息后重试）")
    return {"draft": "\n".join(lines)}


# ============ 共用收尾 ============

async def save_node(state: AgentState) -> dict:
    final_content = state.get("final") or ""
    async with AsyncSessionLocal() as db:
        if state.get("ir_action") != "rejected":
            if state.get("candidate_ids"):
                # 旧·投资人重排：逐人落 outreach_records（与历史行为一致）
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
            else:
                # 方向A·机构名单：整份名单存一条记录（investor_id 为空，drafts 页已支持"无关联"）
                db.add(OutreachRecord(
                    investor_id=None,
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
# 旧·投资人重排分支
builder.add_node("fetch_candidates", fetch_candidates_node)
builder.add_node("rank", rank_node)
builder.add_node("format_list", format_list_node)
# 方向A·机构名单分支
builder.add_node("discover_agencies", discover_agencies_node)
builder.add_node("shortlist", shortlist_node)
builder.add_node("enrich_agencies", enrich_agencies_node)
builder.add_node("agency_rank", agency_rank_node)
builder.add_node("format_agency_list", format_agency_list_node)
# 共用收尾
builder.add_node("review", review_node)
builder.add_node("save", save_node)

builder.add_conditional_edges(START, _route_entry, {
    "investor": "fetch_candidates",
    "agency": "discover_agencies",
})
builder.add_edge("fetch_candidates", "rank")
builder.add_edge("rank", "format_list")
builder.add_edge("format_list", "review")

builder.add_edge("discover_agencies", "shortlist")
builder.add_edge("shortlist", "enrich_agencies")
builder.add_edge("enrich_agencies", "agency_rank")
builder.add_edge("agency_rank", "format_agency_list")
builder.add_edge("format_agency_list", "review")

builder.add_edge("review", "save")
builder.add_edge("save", END)

register_builder("smart_list", builder)

from langgraph.checkpoint.memory import MemorySaver as _MemorySaver
smart_list_graph = builder.compile(checkpointer=_MemorySaver())
