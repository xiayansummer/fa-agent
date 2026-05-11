import json
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models.ir_users import IRUser
from auth.wechat import exchange_code_for_session, decrypt_user_data
from auth.jwt import create_token
from redis_client import get_redis

router = APIRouter()


class LoginRequest(BaseModel):
    code: str


class LoginResponse(BaseModel):
    """Returned when openid is already bound to an IR user."""
    token: str
    ir_id: int
    name: str
    role: str


class NeedBindingResponse(BaseModel):
    """Returned when openid is not yet bound — frontend must call /bind_phone."""
    need_phone_binding: bool = True
    login_session: str


class BindPhoneRequest(BaseModel):
    login_session: str
    encryptedData: str
    iv: str


@router.post("/login", response_model=Union[LoginResponse, NeedBindingResponse])
async def wechat_login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    session = await exchange_code_for_session(body.code)
    openid = session["openid"]
    session_key = session["session_key"]

    result = await db.execute(
        select(IRUser).where(IRUser.wechat_openid == openid, IRUser.is_active == True)
    )
    user = result.scalar_one_or_none()

    if user:
        token = create_token(user.id, user.role)
        return LoginResponse(token=token, ir_id=user.id, name=user.name, role=user.role)

    # openid not bound yet — start phone-binding flow
    login_session = uuid4().hex
    redis = await get_redis()
    # TODO(security): session_key is sensitive (can decrypt all WeChat user data).
    # For production hardening, encrypt with services.crypto_service before Redis storage.
    await redis.setex(
        f"auth:session:{login_session}",
        600,
        json.dumps({"openid": openid, "session_key": session_key}),
    )
    return NeedBindingResponse(login_session=login_session)


@router.post("/bind_phone")
async def bind_phone(body: BindPhoneRequest, db: AsyncSession = Depends(get_db)):
    redis = await get_redis()
    raw = await redis.get(f"auth:session:{body.login_session}")
    if not raw:
        raise HTTPException(status_code=410, detail="session expired")

    session_data = json.loads(raw)
    openid = session_data["openid"]
    session_key = session_data["session_key"]

    try:
        # TODO(security): WeChat recommends validating watermark.appid and watermark.timestamp
        # in the decrypted payload to prevent replay across apps. Acceptable risk for MVP.
        data = decrypt_user_data(body.encryptedData, body.iv, session_key)
        phone = data["phoneNumber"]
    except Exception as exc:
        raise HTTPException(status_code=400, detail="解密失败") from exc

    result = await db.execute(
        select(IRUser).where(IRUser.phone == phone, IRUser.is_active == True)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=403, detail="账号未开通，请联系管理员开通")

    user.wechat_openid = openid
    await db.commit()
    await db.refresh(user)

    await redis.delete(f"auth:session:{body.login_session}")

    token = create_token(user.id, user.role)
    return LoginResponse(token=token, ir_id=user.id, name=user.name, role=user.role)
