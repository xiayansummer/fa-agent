from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from database import get_db
from models.ir_users import IRUser
from auth.jwt import get_current_ir
from services import crypto_service
from services.tencent_meeting import TencentMeetingClient, TencentAuthError, TencentToolError

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


class TencentTokenRequest(BaseModel):
    token: str

class TencentTestResponse(BaseModel):
    ok: bool
    detail: str = ""


@router.put("/tencent", response_model=TencentTestResponse)
async def configure_tencent(
    body: TencentTokenRequest,
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    """验证 token 有效性后 AES 加密入库。"""
    client = TencentMeetingClient(token=body.token)
    if not await client.verify_token():
        raise HTTPException(status_code=400, detail="腾讯会议 token 无效或已过期")

    encrypted = crypto_service.encrypt(body.token)

    result = await db.execute(select(IRUser).where(IRUser.id == current_ir["ir_id"]))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    user.tencent_meeting_token_encrypted = encrypted
    await db.commit()
    return TencentTestResponse(ok=True, detail="已保存")


@router.post("/tencent/test", response_model=TencentTestResponse)
async def test_tencent_token(
    body: TencentTokenRequest,
    _: dict = Depends(get_current_ir),
):
    """仅验证不入库（保存前预检）。"""
    client = TencentMeetingClient(token=body.token)
    ok = await client.verify_token()
    return TencentTestResponse(
        ok=ok,
        detail="" if ok else "token 无效或已过期",
    )
