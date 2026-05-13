from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from auth.jwt import decode_token, get_current_ir
from database import get_db
from agent.runner import run, resume, get_graph
from agent.events import subscribe
from agent.state import AgentState, TaskType, IrAction
from redis_client import get_redis
from skills.claude_skill import _client
from config import settings
from models.ir_users import IRUser
from models.investors import Investor
from models.interaction_logs import InteractionLog
from services import crypto_service
from services.tencent_meeting import TencentMeetingClient, TencentAuthError, TencentToolError
from skills.qmingpian import (
    qmingpian_search_person,
    qmingpian_add_familiar_person,
    qmingpian_update_familiar_person,
    qmingpian_update_person_tags,
    qmingpian_add_person_summary,
)

logger = logging.getLogger(__name__)

router = APIRouter()

THREAD_OWNER_TTL = 3600  # max workflow + IR-review pause window

_SYSTEM_PROMPT = """你是 FA Agent 系统的 Orchestrator（统筹 Agent），是 IR 的核心入口。

# 你的能力分两层

## A. 直接用工具完成（轻量、即时）
- 腾讯会议：schedule / cancel / list
- 投资人：search_investor、set_investor_familiarity、set_investor_tags、
  add_person_summary（写企名片纪要）、record_interaction（记互动）

## B. 分发给专项 Agent（启动工作流，前端会接管显示进度）
- Content Agent → start_meeting_minutes_workflow / start_daily_push_workflow
- Outreach Agent → start_milestone_outreach_workflow
- List Agent → start_smart_list_workflow

# 行为规则
1. 调度/编辑类意图能用工具直接完成时，**不要追问，直接调用**。
2. 用户提到某投资人姓名但你不知道 investor_id 时，**先调 search_investor 拿 ID**。
3. 工具失败时清楚告诉用户原因（错误信息原样展示前缀，再附建议）。
4. 启动 workflow 后，简短告诉用户「已分发给 XX Agent，进度会在下方显示」，**不要重复回答任务本身**。
5. 不确定的投资人信息直接说不知道，不要编造。

回答简洁、具体、可操作。"""


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []  # max 10, older first


class ChatResponse(BaseModel):
    reply: str
    agent_role: str = "orchestrator"  # chat 全部归 Orchestrator 名下，前端按这个着色
    thread_id: Optional[str] = None   # 若触发了 workflow，返回 thread_id 让前端订阅 WS


class RunRequest(BaseModel):
    task_type: TaskType
    meeting_id: Optional[str] = None
    audio_url: Optional[str] = None
    transcript: Optional[str] = None
    tencent_meeting_id: Optional[str] = None  # 新加
    investor_ids: Optional[list[int]] = None
    target_date: Optional[str] = None
    criteria: Optional[str] = None
    candidate_ids: Optional[list[int]] = None
    investor_id: Optional[int] = None
    milestone_type: Optional[str] = None
    ir_name: Optional[str] = None


class ReviewRequest(BaseModel):
    action: IrAction
    final: Optional[str] = None


@router.post("/run")
async def start_workflow(
    request: RunRequest,
    background_tasks: BackgroundTasks,
    current_ir: dict = Depends(get_current_ir),
):
    thread_id = str(uuid.uuid4())
    state: AgentState = {
        "thread_id": thread_id,
        "ir_id": current_ir["ir_id"],
        "task_type": request.task_type,
        "meeting_id": request.meeting_id,
        "audio_url": request.audio_url,
        "transcript": request.transcript,
        "tencent_meeting_id": request.tencent_meeting_id,  # 新加
        "investor_ids": request.investor_ids,
        "investor_profiles": None,
        "target_date": request.target_date,
        "events": None,
        "criteria": request.criteria,
        "candidate_ids": request.candidate_ids,
        "investor_id": request.investor_id,
        "milestone_type": request.milestone_type,
        "ir_name": request.ir_name,
        "draft": None,
        "final": None,
        "ir_action": None,
        "prompt_version": None,
        "skills_called": [],
        "error": None,
        "briefing_signals": None,
        "generated_messages_json": None,
    }
    redis = await get_redis()
    await redis.setex(f"agent:thread:{thread_id}:owner", THREAD_OWNER_TTL, str(current_ir["ir_id"]))
    background_tasks.add_task(run, request.task_type, state, thread_id)
    return {"thread_id": thread_id}


@router.websocket("/ws/{thread_id}")
async def agent_websocket(
    websocket: WebSocket,
    thread_id: str,
    token: Optional[str] = Query(None, description="JWT (alternative to Authorization header)"),
):
    # Mini-program WS may not be able to set Authorization header reliably,
    # so we accept token via header OR ?token= query string.
    auth_header = websocket.headers.get("authorization", "")
    jwt_token = auth_header[7:] if auth_header.lower().startswith("bearer ") else token
    if not jwt_token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="missing token")
        return
    try:
        payload = decode_token(jwt_token)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="invalid token")
        return

    redis = await get_redis()
    owner = await redis.get(f"agent:thread:{thread_id}:owner")
    if not owner:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="thread not found")
        return
    if str(owner) != str(payload["ir_id"]):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="forbidden")
        return

    await websocket.accept()
    try:
        async for event in subscribe(thread_id):
            await websocket.send_json(event)
            if event.get("type") in ("done", "error"):
                break
    except WebSocketDisconnect:
        pass
    finally:
        await websocket.close()


@router.post("/{thread_id}/review")
async def submit_review(
    thread_id: str,
    review: ReviewRequest,
    background_tasks: BackgroundTasks,
    current_ir: dict = Depends(get_current_ir),
):
    redis = await get_redis()
    owner = await redis.get(f"agent:thread:{thread_id}:owner")
    if owner and str(owner) != str(current_ir["ir_id"]):
        raise HTTPException(status_code=403, detail="forbidden")
    task_type = await redis.get(f"agent:thread:{thread_id}:type")
    if not task_type:
        raise HTTPException(status_code=404, detail="Thread not found or already completed")

    ir_decision = {
        "action": review.action,
        "final": review.final or "",
    }
    background_tasks.add_task(resume, task_type, thread_id, ir_decision)
    return {"status": "resumed"}


class StateResponse(BaseModel):
    status: str  # "running" | "waiting_review" | "done" | "error"
    # waiting_review fields
    draft: Optional[str] = None
    task_type: Optional[str] = None
    # done fields
    final: Optional[str] = None
    ir_action: Optional[str] = None
    # running field
    current_node: Optional[str] = None
    # error field
    error: Optional[str] = None


@router.get("/{thread_id}/state", response_model=StateResponse)
async def get_thread_state(
    thread_id: str,
    current_ir: dict = Depends(get_current_ir),
):
    """Snapshot of workflow state — for WS reconnect recovery.
    Schema mirrors WS event payloads so frontend can re-render directly."""
    redis = await get_redis()

    # Check ownership
    owner = await redis.get(f"agent:thread:{thread_id}:owner")
    if not owner:
        raise HTTPException(status_code=404, detail="Thread not found")
    if str(owner) != str(current_ir["ir_id"]):
        raise HTTPException(status_code=403, detail="forbidden")

    # Get task_type to know which graph to query
    task_type = await redis.get(f"agent:thread:{thread_id}:type")
    if not task_type:
        # owner exists but type doesn't — workflow may have just started or was cleaned up
        return StateResponse(status="running")

    try:
        graph = get_graph(task_type)
    except KeyError:
        return StateResponse(status="error", error=f"unknown task_type: {task_type}")

    config = {"configurable": {"thread_id": thread_id}}
    state = graph.get_state(config)
    values = state.values or {}

    # If next includes "review" node and draft is present → waiting_review
    if state.next and "review" in state.next and values.get("draft"):
        return StateResponse(
            status="waiting_review",
            draft=values.get("draft"),
            task_type=task_type,
        )

    # If error in state values → error
    if values.get("error"):
        return StateResponse(status="error", error=values["error"])

    # If next is empty → done
    if not state.next:
        return StateResponse(
            status="done",
            final=values.get("final"),
            ir_action=values.get("ir_action"),
        )

    # Otherwise → running, indicate current/next node
    current = values.get("__current_node__") or (next(iter(state.next)) if state.next else None)
    return StateResponse(status="running", current_node=current)


_CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "schedule_tencent_meeting",
            "description": (
                "在腾讯会议预订/创建一场会议。当 IR 说「安排会议」「约会议」「开个会」等意图时直接调用，"
                "不要追问。subject 根据用户上下文推断；start_time 没明确时用 30 分钟后的下一个整点；"
                "end_time 默认 start_time 后 1 小时。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "会议主题"},
                    "start_time": {"type": "string", "description": "ISO 8601 开始时间，如 2026-05-13T15:30:00+08:00"},
                    "end_time": {"type": "string", "description": "ISO 8601 结束时间，默认 start_time + 1 小时"},
                },
                "required": ["subject", "start_time", "end_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_my_upcoming_meetings",
            "description": (
                "列出当前 IR 即将开始/进行中的腾讯会议，每条含 meeting_id、subject、start_time、end_time。"
                "当 IR 说「取消刚才那场会议」「取消会议」但你不知道 meeting_id 时，先调本工具拿列表，"
                "再用最匹配的那场调 cancel_tencent_meeting。"
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_investor",
            "description": (
                "通过姓名/机构关键字搜索投资人。返回结果含 investor_id (本地ID)、person_id (企名片ID)、"
                "name、agency、position、in_my_library (是否在本地库)。"
                "当用户提到某投资人名字但你不知道 investor_id 时，先调本工具拿 ID 再做其他操作。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {"type": "string", "description": "姓名或机构关键字"},
                },
                "required": ["keywords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_investor_familiarity",
            "description": (
                "设置某投资人的熟悉度。同时写本地数据库 + 企名片。level 必须是 6 个枚举之一：'未接触' / "
                "'加过微信' / '见过面' / '了解投资偏好' / '跟进过我们的项目' / '好友'。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "investor_id": {"type": "integer"},
                    "level": {"type": "string"},
                },
                "required": ["investor_id", "level"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_investor_tags",
            "description": "覆盖式设置投资人在企名片的标签（如「美元」「消费品牌」）。tags=[] 表示清空。",
            "parameters": {
                "type": "object",
                "properties": {
                    "investor_id": {"type": "integer"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["investor_id", "tags"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_person_summary",
            "description": "为某投资人写一条纪要（沟通要点/判断/下一步），保存到企名片。建议 50-300 字。",
            "parameters": {
                "type": "object",
                "properties": {
                    "investor_id": {"type": "integer"},
                    "summary": {"type": "string"},
                },
                "required": ["investor_id", "summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_interaction",
            "description": (
                "记录一条与投资人的互动。type 必须是 'meeting'/'call'/'wechat'/'email'/'push'/'other'。"
                "occurred_at 不传默认现在；duration_min 仅 meeting/call 用；next_followup_at 可选。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "investor_id": {"type": "integer"},
                    "type": {"type": "string"},
                    "summary": {"type": "string"},
                    "occurred_at": {"type": "string", "description": "ISO 8601；不传默认现在"},
                    "duration_min": {"type": "integer"},
                    "next_followup_at": {"type": "string", "description": "ISO 8601，可选"},
                },
                "required": ["investor_id", "type", "summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_meeting_minutes_workflow",
            "description": (
                "启动「会议纪要分析」工作流（Content Agent）。三选一参数：tencent_meeting_id（已开云录制的腾讯会议 ID）"
                " 或 audio_url（已上传的音频公网 URL） 或 transcript（粘贴的文字稿）。"
                "成功后返回 thread_id，前端会自动接管显示进度。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tencent_meeting_id": {"type": "string"},
                    "audio_url": {"type": "string"},
                    "transcript": {"type": "string"},
                    "investor_ids": {"type": "array", "items": {"type": "integer"}, "description": "关联的本地投资人 ID 列表，可选"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_daily_push_workflow",
            "description": (
                "启动「每日跟进推送生成」工作流（Content Agent）。为指定投资人生成个性化跟进消息草稿。"
                "返回 thread_id；前端自动接管。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "investor_ids": {"type": "array", "items": {"type": "integer"}, "description": "目标投资人 ID 列表，不传则取当日所有 followup 事件投资人"},
                    "target_date": {"type": "string", "description": "YYYY-MM-DD，不传默认今天"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_milestone_outreach_workflow",
            "description": (
                "启动「里程碑触达」工作流（Outreach Agent）。为某投资人的生日/入职纪念/首次见面纪念生成祝贺消息。"
                "milestone_type 必须是 'birthday' / 'join_agency' / 'first_meeting'。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "investor_id": {"type": "integer"},
                    "milestone_type": {"type": "string"},
                },
                "required": ["investor_id", "milestone_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_smart_list_workflow",
            "description": (
                "启动「候选投资人推荐」工作流（List Agent）。按 criteria（如行业/阶段/关注领域）从企名片+本地库捞候选并排序。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "criteria": {"type": "string", "description": "筛选条件描述，如『关注 AI 消费、A 轮、人民币基金』"},
                },
                "required": ["criteria"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_tencent_meeting",
            "description": (
                "取消已预订的腾讯会议（不可逆操作！）。\n"
                "调用规则（重要）：\n"
                "1. 用户首次说「取消那场会议」「删掉刚才的会议」等时，**不要直接调用**——先在回复里"
                "复述会议主题/时间/会议号，问用户：「确认取消会议 XXX 吗？」\n"
                "2. 等用户下一轮明确回复「确认」「是」「ok」「取消吧」等之后，再调用本工具。\n"
                "3. 如果用户直接说「确认取消会议号 1234567890」明确给出 meeting_id 且语气坚定，可以一次性调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "meeting_id": {"type": "string", "description": "腾讯会议 meeting_id（数字字符串，从 schedule 结果或 history 取）"},
                    "reason_detail": {"type": "string", "description": "取消原因（可选，默认空字符串）"},
                },
                "required": ["meeting_id"],
            },
        },
    },
]


async def _get_tencent_client(ir_id: int, db: AsyncSession) -> TencentMeetingClient | None:
    """取当前 IR 的腾讯 token 并实例化 client，缺失/解密失败返 None。"""
    user = (await db.execute(select(IRUser).where(IRUser.id == ir_id))).scalar_one_or_none()
    if not user or not user.tencent_meeting_token_encrypted:
        return None
    try:
        token = crypto_service.decrypt(user.tencent_meeting_token_encrypted)
    except Exception:
        return None
    return TencentMeetingClient(token=token)


async def _exec_list_my_upcoming_meetings(ir_id: int, args: dict, db: AsyncSession) -> dict:
    """列出 IR 当前即将开始/进行中的腾讯会议。"""
    client = await _get_tencent_client(ir_id, db)
    if client is None:
        return {"error": "IR 未配置腾讯会议 token"}
    try:
        raw = await client.list_upcoming_meetings()
    except TencentAuthError:
        return {"error": "腾讯会议 token 已失效，请重新配置"}
    except Exception as e:
        return {"error": f"调用失败：{e}"}
    items = []
    for m in raw:
        items.append({
            "meeting_id": str(m.get("meeting_id") or ""),
            "meeting_code": str(m.get("meeting_code") or m.get("meeting_id_str") or ""),
            "subject": m.get("subject") or "",
            "start_time": m.get("start_time") or "",
            "end_time": m.get("end_time") or "",
        })
    return {"ok": True, "meetings": items, "count": len(items)}


async def _exec_cancel_tencent_meeting(ir_id: int, args: dict, db: AsyncSession) -> dict:
    """取消会议 tool。"""
    mid = (args.get("meeting_id") or "").strip()
    if not mid:
        return {"error": "meeting_id 不能为空"}
    client = await _get_tencent_client(ir_id, db)
    if client is None:
        return {"error": "IR 未配置腾讯会议 token"}
    try:
        await client.cancel_meeting(meeting_id=mid, reason_detail=args.get("reason_detail", "") or "")
    except TencentAuthError:
        return {"error": "腾讯会议 token 已失效，请重新配置"}
    except TencentToolError as e:
        return {"error": f"腾讯会议返回错误：{e}"}
    except Exception as e:
        return {"error": f"调用失败：{e}"}
    return {"ok": True, "meeting_id": mid}


async def _resolve_investor(db: AsyncSession, investor_id: int) -> Optional[Investor]:
    res = await db.execute(select(Investor).where(Investor.id == investor_id, Investor.is_active == True))
    return res.scalar_one_or_none()


async def _exec_search_investor(ir_id: int, args: dict, db: AsyncSession) -> dict:
    keywords = (args.get("keywords") or "").strip()
    if not keywords:
        return {"error": "keywords 不能为空"}
    try:
        hits = await qmingpian_search_person(keywords)
    except Exception as e:
        return {"error": f"企名片搜索失败：{e}"}
    person_ids = [h.get("person_id") for h in hits if h.get("person_id")]
    local_map: dict[str, Investor] = {}
    if person_ids:
        rows = (await db.execute(
            select(Investor).where(
                Investor.qmingpian_person_id.in_(person_ids),
                Investor.is_active == True,
            )
        )).scalars().all()
        for inv in rows:
            local_map[inv.qmingpian_person_id] = inv

    # 按 person_id 去重（searchPerson 新鉴权下同 person_id 多条 = 多张名片）
    seen: set[str] = set()
    items = []
    for h in hits:
        pid = h.get("person_id")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        local = local_map.get(pid)
        items.append({
            "investor_id": local.id if local else None,
            "person_id": pid,
            "name": h.get("name", ""),
            "agency": h.get("agency", ""),
            "position": h.get("zhiwu") or "",
            "in_my_library": local is not None,
            "familiarity": local.familiarity if local else None,
        })
    return {"ok": True, "count": len(items), "results": items[:10]}


async def _exec_set_investor_familiarity(ir_id: int, args: dict, db: AsyncSession) -> dict:
    inv_id = args.get("investor_id")
    level = (args.get("level") or "").strip()
    if not inv_id or not level:
        return {"error": "investor_id 和 level 都必填"}
    inv = await _resolve_investor(db, inv_id)
    if not inv:
        return {"error": f"investor_id={inv_id} 不存在"}
    prev = inv.familiarity
    ir_row = (await db.execute(select(IRUser).where(IRUser.id == ir_id))).scalar_one_or_none()
    # 写企名片（如有 username + person_id）
    if ir_row and ir_row.qmingpian_username and inv.qmingpian_person_id:
        try:
            fn = qmingpian_update_familiar_person if prev else qmingpian_add_familiar_person
            await fn(name=inv.name, agency=inv.agency or "", user_name=ir_row.qmingpian_username, level=level)
        except Exception as e:
            logger.warning("familiarity sync to qmingpian failed: %s", e)
            return {"error": f"企名片同步失败：{e}"}
    # 写本地
    inv.familiarity = level
    await db.commit()
    return {"ok": True, "investor_id": inv_id, "name": inv.name, "level": level}


async def _exec_set_investor_tags(ir_id: int, args: dict, db: AsyncSession) -> dict:
    inv_id = args.get("investor_id")
    tags = args.get("tags")
    if not inv_id or tags is None:
        return {"error": "investor_id 和 tags 都必填"}
    if not isinstance(tags, list):
        return {"error": "tags 必须是字符串数组"}
    inv = await _resolve_investor(db, inv_id)
    if not inv:
        return {"error": f"investor_id={inv_id} 不存在"}
    try:
        await qmingpian_update_person_tags(name=inv.name, agency=inv.agency or "", tags=tags)
    except Exception as e:
        return {"error": f"企名片同步失败：{e}"}
    return {"ok": True, "investor_id": inv_id, "name": inv.name, "tags": tags}


async def _exec_add_person_summary(ir_id: int, args: dict, db: AsyncSession) -> dict:
    inv_id = args.get("investor_id")
    summary = (args.get("summary") or "").strip()
    if not inv_id or not summary:
        return {"error": "investor_id 和 summary 都必填"}
    inv = await _resolve_investor(db, inv_id)
    if not inv:
        return {"error": f"investor_id={inv_id} 不存在"}
    ir_row = (await db.execute(select(IRUser).where(IRUser.id == ir_id))).scalar_one_or_none()
    if not ir_row or not ir_row.qmingpian_username:
        return {"error": "当前 IR 未配置企名片用户名（qmingpian_username）"}
    try:
        await qmingpian_add_person_summary(
            name=inv.name, agency=inv.agency or "",
            summary=summary, user_name=ir_row.qmingpian_username,
        )
    except Exception as e:
        return {"error": f"企名片写入纪要失败：{e}"}
    return {"ok": True, "investor_id": inv_id, "name": inv.name, "summary_preview": summary[:60]}


_INTERACTION_TYPES = {"meeting", "call", "wechat", "email", "push", "other"}


async def _exec_record_interaction(ir_id: int, args: dict, db: AsyncSession) -> dict:
    inv_id = args.get("investor_id")
    itype = (args.get("type") or "").strip()
    summary = (args.get("summary") or "").strip()
    if not inv_id or not itype or not summary:
        return {"error": "investor_id, type, summary 都必填"}
    if itype not in _INTERACTION_TYPES:
        return {"error": f"type 必须是 {_INTERACTION_TYPES}"}
    inv = await _resolve_investor(db, inv_id)
    if not inv:
        return {"error": f"investor_id={inv_id} 不存在"}
    occurred_at = args.get("occurred_at")
    try:
        occ_dt = datetime.fromisoformat(occurred_at) if occurred_at else datetime.now()
    except ValueError:
        return {"error": f"occurred_at 格式无效：{occurred_at}"}
    nxt = args.get("next_followup_at")
    try:
        nxt_dt = datetime.fromisoformat(nxt) if nxt else None
    except ValueError:
        return {"error": f"next_followup_at 格式无效：{nxt}"}
    log = InteractionLog(
        investor_id=inv_id, ir_id=ir_id, type=itype,
        occurred_at=occ_dt,
        duration_min=args.get("duration_min"),
        summary=summary,
        next_followup_at=nxt_dt,
        agent_generated=False,
    )
    db.add(log)
    if not inv.last_interaction_at or occ_dt > inv.last_interaction_at:
        inv.last_interaction_at = occ_dt
    await db.commit()
    await db.refresh(log)
    return {"ok": True, "interaction_id": log.id, "investor_id": inv_id, "name": inv.name, "type": itype}


# === 触发 LangGraph workflow 类工具（分发给 Content / Outreach / List Agent） ===

async def _start_workflow(
    ir_id: int,
    task_type: str,
    state_overrides: dict,
) -> dict:
    """统一启动 LangGraph workflow，返回 thread_id。"""
    thread_id = str(uuid.uuid4())
    state: dict = {
        "thread_id": thread_id,
        "ir_id": ir_id,
        "task_type": task_type,
        "meeting_id": None, "audio_url": None, "transcript": None,
        "tencent_meeting_id": None,
        "investor_ids": None, "investor_profiles": None,
        "target_date": None, "events": None,
        "criteria": None, "candidate_ids": None,
        "investor_id": None, "milestone_type": None, "ir_name": None,
        "draft": None, "final": None, "ir_action": None,
        "prompt_version": None, "skills_called": [], "error": None,
        "briefing_signals": None, "generated_messages_json": None,
    }
    state.update(state_overrides)
    redis = await get_redis()
    await redis.setex(f"agent:thread:{thread_id}:owner", THREAD_OWNER_TTL, str(ir_id))
    # background task：在 asyncio loop 里 fire-and-forget
    import asyncio as _asyncio
    _asyncio.create_task(run(task_type, state, thread_id))
    return {"ok": True, "thread_id": thread_id, "task_type": task_type}


async def _exec_start_meeting_minutes(ir_id: int, args: dict, db: AsyncSession) -> dict:
    if not any(args.get(k) for k in ("tencent_meeting_id", "audio_url", "transcript")):
        return {"error": "tencent_meeting_id / audio_url / transcript 至少一个必填"}
    return await _start_workflow(ir_id, "meeting_minutes", {
        "tencent_meeting_id": args.get("tencent_meeting_id"),
        "audio_url": args.get("audio_url"),
        "transcript": args.get("transcript"),
        "investor_ids": args.get("investor_ids"),
    })


async def _exec_start_daily_push(ir_id: int, args: dict, db: AsyncSession) -> dict:
    return await _start_workflow(ir_id, "daily_push", {
        "investor_ids": args.get("investor_ids"),
        "target_date": args.get("target_date") or datetime.now().strftime("%Y-%m-%d"),
    })


async def _exec_start_milestone_outreach(ir_id: int, args: dict, db: AsyncSession) -> dict:
    inv_id = args.get("investor_id")
    mtype = (args.get("milestone_type") or "").strip()
    if not inv_id or mtype not in {"birthday", "join_agency", "first_meeting"}:
        return {"error": "investor_id 必填且 milestone_type ∈ birthday/join_agency/first_meeting"}
    ir_row = (await db.execute(select(IRUser).where(IRUser.id == ir_id))).scalar_one_or_none()
    return await _start_workflow(ir_id, "milestone_outreach", {
        "investor_id": inv_id,
        "milestone_type": mtype,
        "ir_name": ir_row.name if ir_row else "IR",
    })


async def _exec_start_smart_list(ir_id: int, args: dict, db: AsyncSession) -> dict:
    criteria = (args.get("criteria") or "").strip()
    if not criteria:
        return {"error": "criteria 必填"}
    return await _start_workflow(ir_id, "smart_list", {"criteria": criteria})


async def _exec_schedule_tencent_meeting(ir_id: int, args: dict, db: AsyncSession) -> dict:
    """执行 schedule_tencent_meeting tool。返回 {ok, meeting_code, join_url, subject, start_time, end_time} 或 {error}。"""
    user = (await db.execute(select(IRUser).where(IRUser.id == ir_id))).scalar_one_or_none()
    if not user or not user.tencent_meeting_token_encrypted:
        return {"error": "IR 未配置腾讯会议 token，请前往「我」→「腾讯会议接入」配置"}
    try:
        token = crypto_service.decrypt(user.tencent_meeting_token_encrypted)
    except Exception:
        return {"error": "腾讯会议 token 解密失败，请重新配置"}
    client = TencentMeetingClient(token=token)
    try:
        result = await client.schedule_meeting(
            subject=args.get("subject", "").strip() or "FA Agent 预订会议",
            start_time=args["start_time"],
            end_time=args["end_time"],
        )
    except TencentAuthError:
        return {"error": "腾讯会议 token 已失效，请重新配置"}
    except TencentToolError as e:
        return {"error": f"腾讯会议返回错误：{e}"}
    except Exception as e:
        return {"error": f"调用失败：{e}"}

    # 腾讯返回结构：meeting_info_list 数组或顶层字段
    info = result.get("meeting_info_list") or [result]
    first = info[0] if isinstance(info, list) and info else result
    return {
        "ok": True,
        "meeting_code": first.get("meeting_code") or first.get("meeting_id_str") or "",
        "meeting_id": first.get("meeting_id") or "",
        "join_url": first.get("join_url") or "",
        "subject": first.get("subject") or args.get("subject"),
        "start_time": first.get("start_time") or args.get("start_time"),
        "end_time": first.get("end_time") or args.get("end_time"),
    }


@router.post("/chat", response_model=ChatResponse)
async def free_chat(
    body: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    """Free-form chat with the AI; supports tool calling.

    当前可用工具：
      - schedule_tencent_meeting: 预订腾讯会议
    """
    history = body.history[-10:] if body.history else []
    now = datetime.now()
    default_start = (now + timedelta(minutes=30)).replace(second=0, microsecond=0)
    default_end = default_start + timedelta(hours=1)
    system_prompt = (
        f"{_SYSTEM_PROMPT}\n\n"
        f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')} (Asia/Shanghai)\n"
        f"如要预订会议且用户未指定具体时间，默认开始 = {default_start.isoformat()}+08:00，"
        f"结束 = {default_end.isoformat()}+08:00（30 分钟后开 1 小时）。\n"
        "工具调用成功后，用一段简短的人话告诉用户：主题、时间、会议号、入会链接（如果有）。"
    )
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        if msg.role in ("user", "assistant"):
            messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": body.message})

    ir_id = current_ir["ir_id"]
    spawned_thread_id: Optional[str] = None  # workflow 触发后保留 thread_id 给前端
    # 工具调用循环（最多 4 轮，避免死循环）
    for step in range(4):
        resp = await _client.chat.completions.create(
            model=settings.ai_model,
            max_tokens=1024,
            temperature=0.3,
            messages=messages,
            tools=_CHAT_TOOLS,
        )
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            return ChatResponse(reply=msg.content or "", thread_id=spawned_thread_id)
        # 把 assistant 工具调用消息加进 history
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ],
        })
        # 执行每个 tool call，把结果作为 tool message append
        for tc in tool_calls:
            fname = tc.function.name
            try:
                fargs = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                fargs = {}
            if fname == "schedule_tencent_meeting":
                result = await _exec_schedule_tencent_meeting(ir_id, fargs, db)
            elif fname == "cancel_tencent_meeting":
                result = await _exec_cancel_tencent_meeting(ir_id, fargs, db)
            elif fname == "list_my_upcoming_meetings":
                result = await _exec_list_my_upcoming_meetings(ir_id, fargs, db)
            elif fname == "search_investor":
                result = await _exec_search_investor(ir_id, fargs, db)
            elif fname == "set_investor_familiarity":
                result = await _exec_set_investor_familiarity(ir_id, fargs, db)
            elif fname == "set_investor_tags":
                result = await _exec_set_investor_tags(ir_id, fargs, db)
            elif fname == "add_person_summary":
                result = await _exec_add_person_summary(ir_id, fargs, db)
            elif fname == "record_interaction":
                result = await _exec_record_interaction(ir_id, fargs, db)
            elif fname == "start_meeting_minutes_workflow":
                result = await _exec_start_meeting_minutes(ir_id, fargs, db)
                if result.get("thread_id"):
                    spawned_thread_id = result["thread_id"]
            elif fname == "start_daily_push_workflow":
                result = await _exec_start_daily_push(ir_id, fargs, db)
                if result.get("thread_id"):
                    spawned_thread_id = result["thread_id"]
            elif fname == "start_milestone_outreach_workflow":
                result = await _exec_start_milestone_outreach(ir_id, fargs, db)
                if result.get("thread_id"):
                    spawned_thread_id = result["thread_id"]
            elif fname == "start_smart_list_workflow":
                result = await _exec_start_smart_list(ir_id, fargs, db)
                if result.get("thread_id"):
                    spawned_thread_id = result["thread_id"]
            else:
                result = {"error": f"未知工具：{fname}"}
            logger.info("chat tool_call ir=%s tool=%s args=%s result=%s",
                        ir_id, fname, fargs, result)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
        # 继续下一轮让 LLM 总结回复
    return ChatResponse(reply="（工具调用次数超出限制，请重试）")
