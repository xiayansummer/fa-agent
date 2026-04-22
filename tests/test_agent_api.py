import pytest
from httpx import AsyncClient, ASGITransport
from auth.jwt import create_token


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {create_token(ir_id=1, role='ir')}"}


@pytest.mark.asyncio
async def test_run_meeting_minutes(override_db, mocker, auth_headers):
    from main import app
    mock_run = mocker.patch("api.agent.run", new=mocker.AsyncMock())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/agent/run",
            json={
                "task_type": "meeting_minutes",
                "transcript": "会议内容",
                "investor_ids": [1],
            },
            headers=auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "thread_id" in data
    assert mock_run.called


@pytest.mark.asyncio
async def test_run_requires_auth():
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/agent/run", json={"task_type": "meeting_minutes"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_review_endpoint(override_db, mocker, auth_headers):
    from main import app
    mock_resume = mocker.patch("api.agent.resume", new=mocker.AsyncMock())
    mock_redis = mocker.AsyncMock()
    mock_redis.get = mocker.AsyncMock(return_value="meeting_minutes")
    mocker.patch("api.agent.get_redis", new=mocker.AsyncMock(return_value=mock_redis))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/agent/test-thread-001/review",
            json={"action": "approved", "final": "最终内容"},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "resumed"
    assert mock_resume.called


@pytest.mark.asyncio
async def test_review_thread_not_found(override_db, mocker, auth_headers):
    from main import app
    mock_redis = mocker.AsyncMock()
    mock_redis.get = mocker.AsyncMock(return_value=None)
    mocker.patch("api.agent.get_redis", new=mocker.AsyncMock(return_value=mock_redis))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/agent/nonexistent-thread/review",
            json={"action": "approved", "final": ""},
            headers=auth_headers,
        )

    assert resp.status_code == 404
