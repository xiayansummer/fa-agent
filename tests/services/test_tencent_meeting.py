import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from services.tencent_meeting import TencentMeetingClient, TencentAuthError, TencentToolError


@pytest.mark.asyncio
async def test_call_includes_required_headers():
    """验证 header 包含 token 和 skill version"""
    client = TencentMeetingClient(token="test_token_xxx")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "result": {"content": [{"type": "text", "text": '{"foo":"bar"}'}]}
    }

    with patch("services.tencent_meeting.httpx.AsyncClient") as mock_ctx:
        mock_inst = AsyncMock()
        mock_inst.post = AsyncMock(return_value=mock_resp)
        mock_ctx.return_value.__aenter__.return_value = mock_inst

        await client._call("convert_timestamp", {})

    # 验证 post 被调用，带正确 headers
    args, kwargs = mock_inst.post.call_args
    assert kwargs["headers"]["X-Tencent-Meeting-Token"] == "test_token_xxx"
    assert "X-Skill-Version" in kwargs["headers"]
    body = kwargs["json"]
    assert body["jsonrpc"] == "2.0"
    assert body["params"]["name"] == "convert_timestamp"
    # _client_info 自动注入
    assert "_client_info" in body["params"]["arguments"]


@pytest.mark.asyncio
async def test_401_raises_auth_error():
    """HTTP 401 → TencentAuthError"""
    client = TencentMeetingClient(token="bad_token")
    mock_resp = MagicMock(status_code=401)

    with patch("services.tencent_meeting.httpx.AsyncClient") as mock_ctx:
        mock_inst = AsyncMock()
        mock_inst.post = AsyncMock(return_value=mock_resp)
        mock_ctx.return_value.__aenter__.return_value = mock_inst

        with pytest.raises(TencentAuthError):
            await client._call("convert_timestamp", {})


@pytest.mark.asyncio
async def test_jsonrpc_error_raises_tool_error():
    """JSON-RPC error → TencentToolError"""
    client = TencentMeetingClient(token="t")
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "result": {"error": {"code": -32603, "message": "缺少必填参数"}}
    }
    with patch("services.tencent_meeting.httpx.AsyncClient") as mock_ctx:
        mock_inst = AsyncMock()
        mock_inst.post = AsyncMock(return_value=mock_resp)
        mock_ctx.return_value.__aenter__.return_value = mock_inst

        with pytest.raises(TencentToolError) as exc:
            await client._call("get_smart_minutes", {})
        assert "缺少必填参数" in str(exc.value)


@pytest.mark.asyncio
async def test_verify_token_returns_false_on_error():
    client = TencentMeetingClient(token="bad")
    with patch.object(client, "_call", side_effect=TencentAuthError("bad")):
        assert (await client.verify_token()) is False


@pytest.mark.asyncio
async def test_verify_token_returns_true_on_success():
    client = TencentMeetingClient(token="good")
    with patch.object(client, "_call", AsyncMock(return_value={"time_now": "..."})):
        assert (await client.verify_token()) is True


@pytest.mark.asyncio
async def test_list_ended_meetings_unwraps_body():
    """验证多层包装结构（result.content[0].text → JSON → body → JSON）正确解包"""
    client = TencentMeetingClient(token="t")
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "result": {"content": [{"type": "text", "text":
            '{"body":"{\\"meeting_info_list\\":[{\\"meeting_id\\":\\"123\\",\\"subject\\":\\"会议A\\"}]}"}'
        }]}
    }
    with patch("services.tencent_meeting.httpx.AsyncClient") as mock_ctx:
        mock_inst = AsyncMock()
        mock_inst.post = AsyncMock(return_value=mock_resp)
        mock_ctx.return_value.__aenter__.return_value = mock_inst

        result = await client.list_ended_meetings("2026-05-01", "2026-05-31")

    assert len(result) == 1
    assert result[0]["meeting_id"] == "123"
    assert result[0]["subject"] == "会议A"


@pytest.mark.asyncio
async def test_get_records_list_handles_empty():
    """没录制时 body 是 {current_page:1}，应返回空列表"""
    client = TencentMeetingClient(token="t")
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "result": {"content": [{"type": "text", "text": '{"body":"{\\"current_page\\":1}"}'}]}
    }
    with patch("services.tencent_meeting.httpx.AsyncClient") as mock_ctx:
        mock_inst = AsyncMock()
        mock_inst.post = AsyncMock(return_value=mock_resp)
        mock_ctx.return_value.__aenter__.return_value = mock_inst

        result = await client.get_records_list("123")
    assert result == []
