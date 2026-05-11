import pytest
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


@pytest.mark.asyncio(loop_scope="session")
async def test_get_me_returns_user_info(authed_client):
    """Authenticated GET /api/me returns all 6 expected fields."""
    client, user = authed_client
    response = await client.get("/api/me")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == user.id
    assert data["name"] == user.name
    assert data["phone"] == user.phone
    assert data["role"] == user.role
    assert data["wechat_openid"] == user.wechat_openid
    assert "tencent_bound" in data


@pytest.mark.asyncio(loop_scope="session")
async def test_get_me_no_token_returns_401(override_db):
    """Unauthenticated GET /api/me returns 401 or 403."""
    from httpx import AsyncClient, ASGITransport
    from main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/me")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_me_tencent_bound_false_initially(authed_client):
    """Fresh user has no Tencent token, so tencent_bound should be False."""
    client, user = authed_client
    response = await client.get("/api/me")
    assert response.status_code == 200
    data = response.json()
    assert data["tencent_bound"] is False


@pytest.mark.asyncio(loop_scope="session")
async def test_put_tencent_valid_saves(authed_client, db_session, mocker):
    """Valid token → 200, tencent_meeting_token_encrypted set in DB."""
    mocker.patch("api.me.TencentMeetingClient.verify_token", AsyncMock(return_value=True))
    client, user = authed_client
    response = await client.put("/api/me/tencent", json={"token": "valid-token-123"})
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    # Verify DB was updated
    await db_session.refresh(user)
    assert user.tencent_meeting_token_encrypted is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_put_tencent_invalid_rejects(authed_client, db_session, mocker):
    """Invalid token → 400, tencent_meeting_token_encrypted stays None."""
    mocker.patch("api.me.TencentMeetingClient.verify_token", AsyncMock(return_value=False))
    client, user = authed_client
    # Reset the encrypted field first so we know baseline
    user.tencent_meeting_token_encrypted = None
    await db_session.commit()
    response = await client.put("/api/me/tencent", json={"token": "bad-token"})
    assert response.status_code == 400
    # DB should still be empty
    await db_session.refresh(user)
    assert user.tencent_meeting_token_encrypted is None


@pytest.mark.asyncio(loop_scope="session")
async def test_post_tencent_test_valid(authed_client, mocker):
    """POST /tencent/test with valid token returns ok=true, no DB write."""
    mocker.patch("api.me.TencentMeetingClient.verify_token", AsyncMock(return_value=True))
    client, user = authed_client
    response = await client.post("/api/me/tencent/test", json={"token": "valid-token-456"})
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["detail"] == ""


@pytest.mark.asyncio(loop_scope="session")
async def test_post_tencent_test_invalid(authed_client, mocker):
    """POST /tencent/test with invalid token returns ok=false with detail."""
    mocker.patch("api.me.TencentMeetingClient.verify_token", AsyncMock(return_value=False))
    client, user = authed_client
    response = await client.post("/api/me/tencent/test", json={"token": "bad-token-789"})
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["detail"] != ""


def _make_mock_redis(cached_value=None):
    """Return a mock Redis client that simulates get/setex for has_rec cache."""
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=cached_value)
    mock_redis.setex = AsyncMock()
    return mock_redis


@pytest.mark.asyncio(loop_scope="session")
async def test_list_meetings_no_token_returns_422(authed_client, db_session):
    """User without a tencent token → GET /tencent/meetings → 422."""
    client, user = authed_client
    # Ensure no token is set
    user.tencent_meeting_token_encrypted = None
    await db_session.commit()
    response = await client.get("/api/me/tencent/meetings")
    assert response.status_code == 422


@pytest.mark.asyncio(loop_scope="session")
async def test_list_meetings_ended_with_recording(authed_client, db_session, mocker):
    """Ended meeting with a recording → has_recording == True."""
    from services import crypto_service

    client, user = authed_client
    user.tencent_meeting_token_encrypted = crypto_service.encrypt("test_token")
    await db_session.commit()

    raw = [{"meeting_id": "m1", "subject": "S", "start_time": "2026-05-01T09:00:00", "end_time": "2026-05-01T10:00:00"}]
    mocker.patch("api.me.TencentMeetingClient.list_ended_meetings", AsyncMock(return_value=raw))
    mocker.patch("api.me.TencentMeetingClient.get_records_list", AsyncMock(return_value=[{"record_file_id": "r1"}]))
    mock_redis = _make_mock_redis(cached_value=None)  # no cache
    mocker.patch("api.me.get_redis", AsyncMock(return_value=mock_redis))

    response = await client.get("/api/me/tencent/meetings")
    assert response.status_code == 200
    data = response.json()
    assert len(data["meetings"]) == 1
    assert data["meetings"][0]["has_recording"] is True


@pytest.mark.asyncio(loop_scope="session")
async def test_list_meetings_no_recording(authed_client, db_session, mocker):
    """Ended meeting with no recordings → has_recording == False."""
    from services import crypto_service

    client, user = authed_client
    user.tencent_meeting_token_encrypted = crypto_service.encrypt("test_token")
    await db_session.commit()

    raw = [{"meeting_id": "m2", "subject": "S", "start_time": "2026-05-01T09:00:00", "end_time": "2026-05-01T10:00:00"}]
    mocker.patch("api.me.TencentMeetingClient.list_ended_meetings", AsyncMock(return_value=raw))
    mocker.patch("api.me.TencentMeetingClient.get_records_list", AsyncMock(return_value=[]))
    mock_redis = _make_mock_redis(cached_value=None)
    mocker.patch("api.me.get_redis", AsyncMock(return_value=mock_redis))

    response = await client.get("/api/me/tencent/meetings")
    assert response.status_code == 200
    data = response.json()
    assert len(data["meetings"]) == 1
    assert data["meetings"][0]["has_recording"] is False


@pytest.mark.asyncio(loop_scope="session")
async def test_list_meetings_token_expired(authed_client, db_session, mocker):
    """Expired tencent token → list_ended_meetings raises TencentAuthError → 401."""
    from services import crypto_service
    from services.tencent_meeting import TencentAuthError

    client, user = authed_client
    user.tencent_meeting_token_encrypted = crypto_service.encrypt("expired_token")
    await db_session.commit()

    mocker.patch("api.me.TencentMeetingClient.list_ended_meetings", AsyncMock(side_effect=TencentAuthError("expired")))
    mock_redis = _make_mock_redis(cached_value=None)
    mocker.patch("api.me.get_redis", AsyncMock(return_value=mock_redis))

    response = await client.get("/api/me/tencent/meetings")
    assert response.status_code == 401
