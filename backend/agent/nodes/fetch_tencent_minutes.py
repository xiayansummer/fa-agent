"""节点：从腾讯会议拉取智能纪要，写入 state.transcript。

如果没有 tencent_meeting_id，直接 return（pass-through，不影响 transcribe 节点）。
如果有 ID 但没找到录制，抛 RuntimeError，由 workflow 捕获。
"""
from __future__ import annotations
from sqlalchemy import select
from agent.state import AgentState
from database import AsyncSessionLocal
from models.ir_users import IRUser
from services import crypto_service
from services.tencent_meeting import TencentMeetingClient, TencentToolError


async def fetch_tencent_minutes_node(state: AgentState) -> dict:
    tencent_meeting_id = state.get("tencent_meeting_id")
    if not tencent_meeting_id:
        return {}  # pass-through

    # 取 IR 的腾讯 token
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(IRUser).where(IRUser.id == state["ir_id"]))
        user = result.scalar_one_or_none()

    if not user or not user.tencent_meeting_token_encrypted:
        raise RuntimeError("IR 未配置腾讯会议 token")

    token = crypto_service.decrypt(user.tencent_meeting_token_encrypted)
    client = TencentMeetingClient(token=token)

    # 拉录制 → 拿 record_file_id → 拉智能纪要
    records = await client.get_records_list(tencent_meeting_id)
    if not records:
        raise RuntimeError(f"会议 {tencent_meeting_id} 未开云录制，无法拉取纪要")

    # 取第一条录制（一场会议通常只有一段录制）
    record_file_id = records[0].get("record_file_id") or records[0].get("file_id")
    if not record_file_id:
        raise RuntimeError("录制文件无 record_file_id")

    minutes = await client.get_smart_minutes(record_file_id, lang="zh")
    return {
        "transcript": minutes,
        "skills_called": ["腾讯会议.智能纪要"],
    }
