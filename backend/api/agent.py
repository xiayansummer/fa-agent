from __future__ import annotations
import json
import logging
import re
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
from agent import orchestrator_tools
from agent.orchestrator_tools.base import ToolCtx

logger = logging.getLogger(__name__)

router = APIRouter()

THREAD_OWNER_TTL = 3600  # max workflow + IR-review pause window

_SYSTEM_PROMPT = """你是 Orchestrator（统筹 Agent），IR 的核心入口。

身边有 4 个专项 Agent：
- Content Agent：内容生成类工作流（会议纪要分析、每日跟进推送）
- Outreach Agent：里程碑触达 / 外联消息草稿
- List Agent：候选投资人推荐
- 你自己：投资人查询/CRUD、互动记录、企名片纪要、腾讯会议管理

工作方式：判断 IR 意图，选合适工具直接调用——每个工具的 description 已写明用途和参数。
启动其他 Agent 的工作流后，简短告知「已分发」，不要重复任务本身。

附件处理：如果用户消息以「[IR 已上传文档 url=... 文件名=...]」或「[IR 已上传图片 url=... 文件名=...]」开头，
说明 IR 通过聊天框 + 号传了一个文件。规则：

**图片名片**（用户说「关联投资人 X」「这是 X 的名片」）：直接调 bind_business_card(file_url=..., name=...)，
不要先调 search_investor —— bind_business_card 内部已包含"本地查→企名片查→新建→落地→绑名片"完整链路，
即使投资人完全不在系统也能一次完成。

**图片附件会以多模态形式同时传给你**，你能直接看到这张名片。请 OCR 出姓名/机构/职务/手机/邮箱/微信
并作为参数传给 bind_business_card —— 这是从图里读出来的真实信息，不是猜测。
用户消息里如还另外提到（或纠正）某字段，以用户文字为准。

**文档** + 挂到某机构 → 调 add_agency_file(agency, filename, file_url)

都没说明 → 简短反问 IR 想做什么（关联投资人当名片 / 挂机构 / 会议材料 / 其它）"""


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
        "interaction_summary": None,
        "action_items": None,
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



# 匹配前端注入的图片/文档前缀 —— url 非贪婪到下一个 ` 文件名=`
_IMG_MARK_RE = re.compile(r"\[IR 已上传图片 url=(\S+?) 文件名=(.+?)\]\s*")
_DOC_MARK_RE = re.compile(r"\[IR 已上传文档 url=(\S+?) 文件名=(.+?)\]\s*")


def _build_user_content(text: str):
    """图片附件 → 拆成 multi-content（让 Qwen-VL 看图直接 OCR）。
    文档附件 / 无附件 → 原样字符串返回。"""
    m = _IMG_MARK_RE.search(text)
    if not m:
        return text
    img_url, filename = m.group(1), m.group(2)
    rest = (text[:m.start()] + text[m.end():]).strip()
    instruction = (
        f"用户上传了一张图片（文件名 {filename}），如果是名片请直接 OCR 出"
        f"姓名/机构/职务/手机/邮箱/微信等字段，然后据此选择并调用合适工具（首选 bind_business_card）。"
    )
    if rest:
        instruction += f"\n用户附加说明：{rest}"
    return [
        {"type": "text", "text": instruction},
        {"type": "image_url", "image_url": {"url": img_url}},
    ]


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
    messages.append({"role": "user", "content": _build_user_content(body.message)})

    ir_id = current_ir["ir_id"]
    spawned_thread_id: Optional[str] = None  # workflow 触发后保留 thread_id 给前端
    # 工具调用循环（最多 4 轮，避免死循环）
    for step in range(4):
        resp = await _client.chat.completions.create(
            model=settings.ai_model,
            max_tokens=1024,
            temperature=0.3,
            messages=messages,
            tools=orchestrator_tools.ALL_TOOLS,
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
        # 执行每个 tool call —— 按 orchestrator_tools 注册路由分发
        ctx = ToolCtx(ir_id=ir_id, db=db)
        for tc in tool_calls:
            fname = tc.function.name
            try:
                fargs = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                fargs = {}
            owner = orchestrator_tools.TOOL_OWNER.get(fname)
            if owner is None:
                result = {"error": f"未知工具：{fname}"}
            else:
                result = await owner.dispatch(fname, fargs, ctx)
            # workflow 触发类工具会返回 thread_id —— 抽出来给前端
            if isinstance(result, dict) and result.get("thread_id"):
                spawned_thread_id = result["thread_id"]
            logger.info("chat tool_call ir=%s tool=%s args=%s result=%s",
                        ir_id, fname, fargs, result)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
        # 继续下一轮让 LLM 总结回复
    return ChatResponse(reply="（工具调用次数超出限制，请重试）")
