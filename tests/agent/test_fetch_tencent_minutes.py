import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agent.nodes.fetch_tencent_minutes import fetch_tencent_minutes_node


@pytest.mark.asyncio
async def test_no_tencent_id_passes_through():
    """没 tencent_meeting_id 时直接返回空 dict，不影响 transcribe"""
    state = {"ir_id": 1, "tencent_meeting_id": None}
    result = await fetch_tencent_minutes_node(state)
    assert result == {}


@pytest.mark.asyncio
async def test_fetches_minutes_when_recording_exists(mocker):
    """有 ID + 有录制 → 调 MCP 拿纪要 → 写入 transcript"""
    from models.ir_users import IRUser

    # mock DB session
    mock_user = IRUser(id=1, name="X", tencent_meeting_token_encrypted=b"encrypted_blob")
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_user
    mock_db.execute = AsyncMock(return_value=mock_result)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    mocker.patch("agent.nodes.fetch_tencent_minutes.AsyncSessionLocal", return_value=mock_session_ctx)
    mocker.patch("agent.nodes.fetch_tencent_minutes.crypto_service.decrypt", return_value="real_token")

    mock_client = MagicMock()
    mock_client.get_records_list = AsyncMock(return_value=[{"record_file_id": "rf1"}])
    mock_client.get_smart_minutes = AsyncMock(return_value="会议要点：...")
    mocker.patch("agent.nodes.fetch_tencent_minutes.TencentMeetingClient", return_value=mock_client)

    state = {"ir_id": 1, "tencent_meeting_id": "m1"}
    result = await fetch_tencent_minutes_node(state)

    assert result["transcript"] == "会议要点：..."
    assert "腾讯会议.智能纪要" in result["skills_called"]


@pytest.mark.asyncio
async def test_no_recording_raises(mocker):
    """有 ID 但无录制 → RuntimeError"""
    from models.ir_users import IRUser

    mock_user = IRUser(id=1, name="X", tencent_meeting_token_encrypted=b"e")
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_user
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
    mocker.patch("agent.nodes.fetch_tencent_minutes.AsyncSessionLocal", return_value=mock_session_ctx)
    mocker.patch("agent.nodes.fetch_tencent_minutes.crypto_service.decrypt", return_value="t")

    mock_client = MagicMock()
    mock_client.get_records_list = AsyncMock(return_value=[])  # 空
    mocker.patch("agent.nodes.fetch_tencent_minutes.TencentMeetingClient", return_value=mock_client)

    state = {"ir_id": 1, "tencent_meeting_id": "m1"}
    with pytest.raises(RuntimeError, match="未开云录制"):
        await fetch_tencent_minutes_node(state)


@pytest.mark.asyncio
async def test_no_token_raises(mocker):
    """IR 未配 token → RuntimeError"""
    from models.ir_users import IRUser

    mock_user = IRUser(id=1, name="X", tencent_meeting_token_encrypted=None)
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_user
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
    mocker.patch("agent.nodes.fetch_tencent_minutes.AsyncSessionLocal", return_value=mock_session_ctx)

    state = {"ir_id": 1, "tencent_meeting_id": "m1"}
    with pytest.raises(RuntimeError, match="未配置腾讯会议 token"):
        await fetch_tencent_minutes_node(state)
