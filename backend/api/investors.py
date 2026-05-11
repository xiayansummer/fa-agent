from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from typing import Optional
from datetime import datetime
from datetime import date as date_type
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

class InvestorCreate(BaseModel):
    name: str
    agency: Optional[str] = None
    position: Optional[str] = None
    email: Optional[list] = None
    wechat: Optional[list] = None
    phone: Optional[list] = None
    industry_tags: Optional[list] = None
    stage_pref: Optional[list] = None
    quota_range: Optional[str] = None
    relationship_score: int = 0
    profile_notes: Optional[str] = None
    birthday: Optional[date_type] = None
    join_agency_date: Optional[date_type] = None
    first_meeting_date: Optional[date_type] = None

class InvestorUpdate(BaseModel):
    name: Optional[str] = None
    agency: Optional[str] = None
    position: Optional[str] = None
    email: Optional[list] = None
    wechat: Optional[list] = None
    phone: Optional[list] = None
    industry_tags: Optional[list] = None
    stage_pref: Optional[list] = None
    quota_range: Optional[str] = None
    relationship_score: Optional[int] = None
    profile_notes: Optional[str] = None
    birthday: Optional[date_type] = None
    join_agency_date: Optional[date_type] = None
    first_meeting_date: Optional[date_type] = None

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

@router.post("", response_model=InvestorOut)
async def create_investor(
    body: InvestorCreate,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_ir),
):
    investor = Investor(**body.model_dump())
    db.add(investor)
    await db.commit()
    await db.refresh(investor)
    return investor

@router.put("/{investor_id}", response_model=InvestorOut)
async def update_investor(
    investor_id: int,
    body: InvestorUpdate,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_ir),
):
    result = await db.execute(select(Investor).where(Investor.id == investor_id))
    investor = result.scalar_one_or_none()
    if not investor:
        raise HTTPException(status_code=404, detail="投资人不存在")
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(investor, field, value)
    await db.commit()
    await db.refresh(investor)
    return investor

@router.delete("/{investor_id}")
async def delete_investor(
    investor_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_ir),
):
    result = await db.execute(select(Investor).where(Investor.id == investor_id))
    investor = result.scalar_one_or_none()
    if not investor or not investor.is_active:
        raise HTTPException(status_code=404, detail="投资人不存在或已删除")
    investor.is_active = False
    await db.commit()
    return {"deleted": True}
