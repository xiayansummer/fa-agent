from datetime import datetime
from typing import Optional, Literal
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from database import get_db
from models.outreach_records import OutreachRecord
from auth.jwt import get_current_ir

router = APIRouter()

class OutreachOut(BaseModel):
    id: int
    investor_id: int
    type: str
    channel: str
    content: Optional[str] = None
    status: str
    created_at: datetime
    sent_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

@router.get("/pending", response_model=list[OutreachOut])
async def list_pending(
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    """Drafts awaiting IR review (status=draft) for current user."""
    result = await db.execute(
        select(OutreachRecord)
        .where(OutreachRecord.ir_id == current_ir["ir_id"])
        .where(OutreachRecord.status == "draft")
        .order_by(OutreachRecord.created_at.desc())
    )
    return list(result.scalars().all())

@router.get("/history", response_model=list[OutreachOut])
async def list_history(
    status: Optional[Literal["draft","approved","sent","failed"]] = Query(None),
    type: Optional[Literal["meeting_minutes","industry_report","daily_push","milestone_message"]] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    """All outreach records for current user, paginated, optional status/type filter."""
    stmt = select(OutreachRecord).where(OutreachRecord.ir_id == current_ir["ir_id"])
    if status:
        stmt = stmt.where(OutreachRecord.status == status)
    if type:
        stmt = stmt.where(OutreachRecord.type == type)
    stmt = stmt.order_by(OutreachRecord.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
