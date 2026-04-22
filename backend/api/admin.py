from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from database import get_db
from models.ir_users import IRUser
from auth.jwt import require_admin

router = APIRouter()

class CreateUserRequest(BaseModel):
    name: str
    phone: Optional[str] = None
    role: str = "ir"

class BindOpenidRequest(BaseModel):
    wechat_openid: str

class UserOut(BaseModel):
    id: int
    name: str
    phone: Optional[str] = None
    role: str
    wechat_openid: Optional[str] = None
    is_active: bool

    model_config = {"from_attributes": True}

@router.post("/users", response_model=UserOut)
async def create_user(
    body: CreateUserRequest,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_admin),
):
    user = IRUser(name=body.name, phone=body.phone, role=body.role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

@router.post("/users/{user_id}/bind", response_model=UserOut)
async def bind_openid(
    user_id: int,
    body: BindOpenidRequest,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_admin),
):
    result = await db.execute(select(IRUser).where(IRUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.wechat_openid = body.wechat_openid
    await db.commit()
    await db.refresh(user)
    return user

@router.patch("/users/{user_id}")
async def toggle_user(
    user_id: int,
    is_active: bool,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_admin),
):
    result = await db.execute(select(IRUser).where(IRUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.is_active = is_active
    await db.commit()
    return {"ok": True}
