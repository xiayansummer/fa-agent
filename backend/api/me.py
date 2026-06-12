from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional, Literal
from datetime import datetime, timedelta
from database import get_db
from models.ir_users import IRUser
from auth.jwt import get_current_ir
from services import crypto_service
from services.tencent_meeting import TencentMeetingClient, TencentAuthError, TencentToolError
from redis_client import get_redis

router = APIRouter()

class MeResponse(BaseModel):
    id: int
    name: str
    phone: Optional[str] = None
    role: str
    wechat_openid: Optional[str] = None
    tencent_bound: bool
    qmingpian_username: Optional[str] = None


class MeUpdate(BaseModel):
    """用户自己能改的字段（不含 phone — phone 绑定后不可改）。"""
    name: Optional[str] = None
    qmingpian_username: Optional[str] = None


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
        qmingpian_username=user.qmingpian_username,
    )


@router.put("", response_model=MeResponse)
async def update_me(
    body: MeUpdate,
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    """IR 自己更新部分信息（名字 / 企名片用户名）。手机号不可改。"""
    result = await db.execute(select(IRUser).where(IRUser.id == current_ir["ir_id"]))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return MeResponse(
        id=user.id,
        name=user.name,
        phone=user.phone,
        role=user.role,
        wechat_openid=user.wechat_openid,
        tencent_bound=bool(user.tencent_meeting_token_encrypted),
        qmingpian_username=user.qmingpian_username,
    )


class TencentTokenRequest(BaseModel):
    token: str


class TencentTestRequest(BaseModel):
    token: Optional[str] = None  # 不传 → 用 db 里已保存的 token 验

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
    body: TencentTestRequest,
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    """验证 token 是否可用（不入库）。
    - 传 token：验那个新 token（保存前预检）
    - 不传 token：用 db 里已保存的 token 验（已配置状态下复测）"""
    token = (body.token or "").strip()
    if not token:
        user = (await db.execute(
            select(IRUser).where(IRUser.id == current_ir["ir_id"])
        )).scalar_one_or_none()
        if not user or not user.tencent_meeting_token_encrypted:
            return TencentTestResponse(ok=False, detail="尚未保存 token")
        try:
            token = crypto_service.decrypt(user.tencent_meeting_token_encrypted)
        except Exception:
            return TencentTestResponse(ok=False, detail="已保存 token 解密失败，请重新填写")
    client = TencentMeetingClient(token=token)
    ok = await client.verify_token()
    return TencentTestResponse(
        ok=ok,
        detail="" if ok else "token 无效或已过期",
    )


class SubscribeReport(BaseModel):
    template_id: Optional[str] = None  # 不传默认日程提醒模板


@router.post("/subscribe")
async def report_subscribe_grant(
    body: SubscribeReport,
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    """前端 wx.requestSubscribeMessage 用户「允许」后上报——本地配额 +1。
    一次性订阅消息平台规则：一次授权 = 一条发送配额。"""
    from config import settings as _s
    from models.wx_sub_quota import WxSubQuota
    tmpl = (body.template_id or "").strip() or _s.wx_schedule_tmpl_id
    row = (await db.execute(
        select(WxSubQuota).where(
            WxSubQuota.ir_id == current_ir["ir_id"],
            WxSubQuota.template_id == tmpl,
        )
    )).scalar_one_or_none()
    if row is None:
        row = WxSubQuota(ir_id=current_ir["ir_id"], template_id=tmpl, times=1)
        db.add(row)
    else:
        row.times = (row.times or 0) + 1
    await db.commit()
    return {"ok": True, "times": row.times}


class MeetingRecordCheck(BaseModel):
    meeting_id: str
    has_recording: bool


@router.get("/tencent/meetings/{meeting_id}/records", response_model=MeetingRecordCheck)
async def check_meeting_recording(
    meeting_id: str,
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    """单场会议是否开了云录制——前端在自动发起腾讯纪要前先探测。"""
    result = await db.execute(select(IRUser).where(IRUser.id == current_ir["ir_id"]))
    user = result.scalar_one_or_none()
    if not user or not user.tencent_meeting_token_encrypted:
        raise HTTPException(status_code=422, detail="请先在「我」-「腾讯会议接入」配置 token")
    try:
        token = crypto_service.decrypt(user.tencent_meeting_token_encrypted)
    except ValueError:
        raise HTTPException(status_code=500, detail="token 解密失败，请重新配置")
    client = TencentMeetingClient(token=token)
    try:
        records = await client.get_records_list(meeting_id)
        has_rec = len(records) > 0
    except TencentToolError:
        has_rec = False
    except TencentAuthError:
        raise HTTPException(status_code=401, detail="腾讯会议 token 已失效，请重新配置")
    return MeetingRecordCheck(meeting_id=meeting_id, has_recording=has_rec)


class TencentMeetingItem(BaseModel):
    meeting_id: str
    subject: str
    start_time: str
    end_time: str
    has_recording: bool

class TencentMeetingsResponse(BaseModel):
    meetings: list[TencentMeetingItem]


@router.get("/tencent/meetings", response_model=TencentMeetingsResponse)
async def list_tencent_meetings(
    status: Literal["ended", "upcoming"] = "ended",
    days: int = 31,
    db: AsyncSession = Depends(get_db),
    current_ir: dict = Depends(get_current_ir),
):
    """从腾讯会议拉取 IR 的会议列表，含 has_recording 检查（Redis 5min 缓存）。"""
    # 解密 token
    result = await db.execute(select(IRUser).where(IRUser.id == current_ir["ir_id"]))
    user = result.scalar_one_or_none()
    if not user or not user.tencent_meeting_token_encrypted:
        raise HTTPException(status_code=422, detail="请先在「我」-「腾讯会议接入」配置 token")

    try:
        token = crypto_service.decrypt(user.tencent_meeting_token_encrypted)
    except ValueError:
        raise HTTPException(status_code=500, detail="token 解密失败，请重新配置")

    client = TencentMeetingClient(token=token)

    try:
        if status == "ended":
            end_time = datetime.now()
            start_time = end_time - timedelta(days=days)
            raw_meetings = await client.list_ended_meetings(
                start_time=start_time.strftime("%Y-%m-%d %H:%M:%S"),
                end_time=end_time.strftime("%Y-%m-%d %H:%M:%S"),
            )
        else:
            raw_meetings = await client.list_upcoming_meetings()
    except TencentAuthError:
        raise HTTPException(status_code=401, detail="腾讯会议 token 已失效，请重新配置")
    except TencentToolError as e:
        raise HTTPException(status_code=502, detail=f"腾讯会议 API 错误: {e}")

    # has_recording 检查（Redis 缓存 5 分钟）
    redis = await get_redis()
    meetings = []
    for m in raw_meetings:
        meeting_id = str(m.get("meeting_id", ""))
        cache_key = f"tencent:has_rec:{meeting_id}"
        cached = await redis.get(cache_key)
        if cached is not None:
            has_rec = cached == "1"
        else:
            try:
                records = await client.get_records_list(meeting_id)
                has_rec = len(records) > 0
            except TencentToolError:
                has_rec = False
            await redis.setex(cache_key, 300, "1" if has_rec else "0")

        meetings.append(TencentMeetingItem(
            meeting_id=meeting_id,
            subject=m.get("subject", ""),
            start_time=m.get("start_time", ""),
            end_time=m.get("end_time", ""),
            has_recording=has_rec,
        ))

    return TencentMeetingsResponse(meetings=meetings)
