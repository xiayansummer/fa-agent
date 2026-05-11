"""Tests for POST /api/agent/chat (free-form chat endpoint)."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport
from auth.jwt import create_token


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {create_token(ir_id=1, role='ir')}"}


def _make_mock_response(content: str):
    """Build a mock OpenAI chat completion response with the given content."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=content))]
    return mock_response


@pytest.mark.asyncio
async def test_chat_simple_message(override_db, mocker, auth_headers):
    """POST /chat with a plain message returns 200 and the LLM reply."""
    from main import app

    mocker.patch(
        "skills.claude_skill._client.chat.completions.create",
        new=AsyncMock(return_value=_make_mock_response("canned reply")),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/agent/chat",
            json={"message": "你好，帮我介绍一下高瓴资本"},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    assert resp.json()["reply"] == "canned reply"


@pytest.mark.asyncio
async def test_chat_with_history(override_db, mocker, auth_headers):
    """POST /chat with 3-message history: verify LLM called with system + 3 history + 1 new (5 total)."""
    from main import app

    mock_create = AsyncMock(return_value=_make_mock_response("historical reply"))
    mocker.patch("skills.claude_skill._client.chat.completions.create", new=mock_create)

    history = [
        {"role": "user", "content": "第一条消息"},
        {"role": "assistant", "content": "第一条回复"},
        {"role": "user", "content": "第二条消息"},
    ]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/agent/chat",
            json={"message": "新的问题", "history": history},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    assert resp.json()["reply"] == "historical reply"

    # Verify messages passed to LLM: system + 3 history + 1 new user = 5
    call_kwargs = mock_create.call_args
    messages = call_kwargs.kwargs.get("messages") or call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs["messages"]
    # extract messages from kwargs
    messages = mock_create.call_args.kwargs["messages"]
    assert len(messages) == 5
    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "第一条消息"}
    assert messages[2] == {"role": "assistant", "content": "第一条回复"}
    assert messages[3] == {"role": "user", "content": "第二条消息"}
    assert messages[4] == {"role": "user", "content": "新的问题"}


@pytest.mark.asyncio
async def test_chat_history_capped_at_10(override_db, mocker, auth_headers):
    """History of 15 messages is capped to last 10; LLM receives system + 10 history + 1 new = 12 total."""
    from main import app

    mock_create = AsyncMock(return_value=_make_mock_response("capped reply"))
    mocker.patch("skills.claude_skill._client.chat.completions.create", new=mock_create)

    # Build 15 alternating messages
    history = []
    for i in range(15):
        role = "user" if i % 2 == 0 else "assistant"
        history.append({"role": role, "content": f"消息 {i}"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/agent/chat",
            json={"message": "最新问题", "history": history},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    messages = mock_create.call_args.kwargs["messages"]
    # system(1) + last 10 history + new user message(1) = 12
    assert len(messages) == 12
    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "最新问题"}
    # Verify it's the LAST 10 messages (indices 5-14 of original history)
    assert messages[1]["content"] == "消息 5"


@pytest.mark.asyncio
async def test_chat_no_auth_returns_401():
    """Request without Authorization header is rejected with 401 or 403."""
    from main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/agent/chat",
            json={"message": "未授权请求"},
        )

    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_chat_empty_history_works(override_db, mocker, auth_headers):
    """Empty history list still works: LLM receives system prompt + user message only (2 total)."""
    from main import app

    mock_create = AsyncMock(return_value=_make_mock_response("direct reply"))
    mocker.patch("skills.claude_skill._client.chat.completions.create", new=mock_create)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/agent/chat",
            json={"message": "直接提问", "history": []},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    assert resp.json()["reply"] == "direct reply"

    messages = mock_create.call_args.kwargs["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "直接提问"}
