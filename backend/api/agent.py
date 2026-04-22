from __future__ import annotations
import uuid
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from auth.jwt import get_current_ir
from agent.runner import run, resume
from agent.events import subscribe
from agent.state import AgentState, TaskType, IrAction
from redis_client import get_redis

router = APIRouter()


class RunRequest(BaseModel):
    task_type: TaskType
    meeting_id: Optional[str] = None
    audio_url: Optional[str] = None
    transcript: Optional[str] = None
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
    }
    background_tasks.add_task(run, request.task_type, state, thread_id)
    return {"thread_id": thread_id}


@router.websocket("/ws/{thread_id}")
async def agent_websocket(websocket: WebSocket, thread_id: str):
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
    task_type = await redis.get(f"agent:thread:{thread_id}:type")
    if not task_type:
        raise HTTPException(status_code=404, detail="Thread not found or already completed")

    ir_decision = {
        "action": review.action,
        "final": review.final or "",
    }
    background_tasks.add_task(resume, task_type, thread_id, ir_decision)
    return {"status": "resumed"}
