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
from services import crypto_service
from services.tencent_meeting import TencentMeetingClient, TencentAuthError, TencentToolError

logger = logging.getLogger(__name__)

router = APIRouter()

THREAD_OWNER_TTL = 3600  # max workflow + IR-review pause window

_SYSTEM_PROMPT = """你是 FA Agent 的对话助手，帮助 IR（投资人关系经理）回答关于投资人、市场、跟进策略的问题。
回答要简洁、具体、可操作。如果不确定具体投资人信息，直接说不知道，不要编造。"""


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []  # max 10, older first


class ChatResponse(BaseModel):
    reply: str


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
]


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
            return ChatResponse(reply=msg.content or "")
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
