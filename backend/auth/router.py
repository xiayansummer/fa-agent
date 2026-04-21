from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models.ir_users import IRUser
from auth.wechat import exchange_code_for_openid
from auth.jwt import create_token

router = APIRouter()

class LoginRequest(BaseModel):
    code: str

class LoginResponse(BaseModel):
    token: str
    ir_id: int
    name: str
    role: str

@router.post("/login", response_model=LoginResponse)
async def wechat_login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    openid = await exchange_code_for_openid(body.code)

    result = await db.execute(
        select(IRUser).where(IRUser.wechat_openid == openid, IRUser.is_active == True)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=403, detail="账号未开通，请联系管理员")

    token = create_token(user.id, user.role)
    return LoginResponse(token=token, ir_id=user.id, name=user.name, role=user.role)
