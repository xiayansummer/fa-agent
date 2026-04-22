from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from typing import Optional
from datetime import datetime
from pydantic import BaseModel
from database import get_db
from models.investors import Investor
from auth.jwt import get_current_ir

router = APIRouter()

class InvestorOut(BaseModel):
    id: int
    name: str
    agency: Optional[str] = None
    position: Optional[str] = None
    industry_tags: Optional[list] = None
    stage_pref: Optional[list] = None
    relationship_score: int = 0
    profile_notes: Optional[str] = None
    last_interaction_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

class InvestorListOut(BaseModel):
    items: list[InvestorOut]
    total: int

@router.get("", response_model=InvestorListOut)
async def list_investors(
    stage: Optional[str] = Query(None),
    industry: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_ir),
):
    stmt = select(Investor).where(Investor.is_active == True)
    if q:
        stmt = stmt.where(or_(
            Investor.name.contains(q),
            Investor.agency.contains(q),
        ))
    if stage:
        stmt = stmt.where(Investor.stage_pref.contains(f'"{stage}"'))
    if industry:
        stmt = stmt.where(Investor.industry_tags.contains(f'"{industry}"'))

    result = await db.execute(stmt)
    investors = result.scalars().all()
    return InvestorListOut(items=list(investors), total=len(investors))

@router.get("/{investor_id}", response_model=InvestorOut)
async def get_investor(
    investor_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_ir),
):
    result = await db.execute(select(Investor).where(Investor.id == investor_id))
    investor = result.scalar_one_or_none()
    if not investor:
        raise HTTPException(status_code=404, detail="投资人不存在")
    return investor
