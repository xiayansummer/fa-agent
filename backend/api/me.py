from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from database import get_db
from models.ir_users import IRUser
from auth.jwt import get_current_ir

router = APIRouter()

class MeResponse(BaseModel):
    id: int
    name: str
    phone: Optional[str] = None
    role: str
    wechat_openid: Optional[str] = None
    tencent_bound: bool

@router.get("", response_model=MeResponse)
async def get_me(
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    result = await db.execute(select(IRUser).where(IRUser.id == current_ir["ir_id"]))
    user = result.scalar_one_or_none()
    if not user:
        # JWT valid but user record gone — should not happen normally
        raise HTTPException(status_code=404, detail="user not found")
    return MeResponse(
        id=user.id,
        name=user.name,
        phone=user.phone,
        role=user.role,
        wechat_openid=user.wechat_openid,
        tencent_bound=bool(user.tencent_meeting_token_encrypted),
    )
