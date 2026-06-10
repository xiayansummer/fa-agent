from datetime import datetime
from typing import Optional, Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from database import get_db
from models.investors import Investor
from models.interaction_logs import InteractionLog
from auth.jwt import get_current_ir

router = APIRouter()

class InteractionCreate(BaseModel):
    type: Literal["meeting","email","wechat","push","call","other"]
    occurred_at: datetime
    duration_min: Optional[int] = None
    summary: str
    next_followup_at: Optional[datetime] = None

class InteractionOut(BaseModel):
    id: int
    investor_id: int
    ir_id: int
    type: str
    occurred_at: datetime
    duration_min: Optional[int] = None
    summary: Optional[str] = None
    next_followup_at: Optional[datetime] = None
    created_at: datetime
    agent_generated: bool

    model_config = {"from_attributes": True}

@router.post("/{investor_id}/interactions", response_model=InteractionOut)
async def create_interaction(
    investor_id: int,
    body: InteractionCreate,
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    # Verify investor exists
    result = await db.execute(select(Investor).where(Investor.id == investor_id))
    investor = result.scalar_one_or_none()
    if not investor:
        raise HTTPException(status_code=404, detail="投资人不存在")

    log = InteractionLog(
        investor_id=investor_id,
        ir_id=current_ir["ir_id"],
        type=body.type,
        occurred_at=body.occurred_at,
        duration_min=body.duration_min,
        summary=body.summary,
        next_followup_at=body.next_followup_at,
        agent_generated=False,
    )
    db.add(log)

    # Update investor's last_interaction_at if newer
    if not investor.last_interaction_at or body.occurred_at > investor.last_interaction_at:
        investor.last_interaction_at = body.occurred_at

    await db.commit()
    await db.refresh(log)
    return log

@router.delete("/{investor_id}/interactions/{interaction_id}")
async def delete_interaction(
    investor_id: int,
    interaction_id: int,
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    result = await db.execute(
        select(InteractionLog).where(
            InteractionLog.id == interaction_id,
            InteractionLog.investor_id == investor_id,
        )
    )
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="互动记录不存在")
    if log.ir_id != current_ir["ir_id"]:
        raise HTTPException(status_code=403, detail="只能删除自己记录的互动")

    await db.delete(log)
    await db.flush()
    # 删掉最近一条后，重算投资人的 last_interaction_at（取剩余互动的最大 occurred_at）
    new_last = (await db.execute(
        select(func.max(InteractionLog.occurred_at))
        .where(InteractionLog.investor_id == investor_id)
    )).scalar()
    investor = (await db.execute(
        select(Investor).where(Investor.id == investor_id)
    )).scalar_one_or_none()
    if investor:
        investor.last_interaction_at = new_last
    await db.commit()
    return {"deleted": True}


@router.get("/{investor_id}/interactions", response_model=list[InteractionOut])
async def list_interactions(
    investor_id: int,
    limit: int = Query(5, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_ir),
):
    result = await db.execute(
        select(InteractionLog)
        .where(InteractionLog.investor_id == investor_id)
        .order_by(InteractionLog.occurred_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
