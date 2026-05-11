"""Tests for B3: Phone-binding login flow.

Tests:
- test_login_bound_user_returns_token
- test_login_unbound_returns_need_phone_binding
- test_bind_phone_matches_writes_openid
- test_bind_phone_no_match_returns_403
- test_bind_phone_expired_session_returns_410
"""
import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_redis(stored_value=None):
    """Return an AsyncMock Redis with get/setex/delete pre-configured."""
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=stored_value)
    mock_redis.setex = AsyncMock()
    mock_redis.delete = AsyncMock()
    return mock_redis


# ---------------------------------------------------------------------------
# /login tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_bound_user_returns_token(db_session, override_db):
    """User already has openid in DB → /login returns JWT token directly."""
    from models.ir_users import IRUser

    user = IRUser(name="已绑定IR", phone="13900000001", role="ir", wechat_openid="bound_openid_001", is_active=True)
    db_session.add(user)
    await db_session.commit()

    mock_session = {"openid": "bound_openid_001", "session_key": "someKey="}
    with patch("auth.router.exchange_code_for_session", AsyncMock(return_value=mock_session)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/auth/login", json={"code": "wx_code_123"})

    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["ir_id"] == user.id
    assert data["name"] == "已绑定IR"
    assert data["role"] == "ir"
    assert "need_phone_binding" not in data


@pytest.mark.asyncio
async def test_login_unbound_returns_need_phone_binding(override_db):
    """New openid not in DB → /login returns need_phone_binding=True + login_session."""
    mock_session = {"openid": "brand_new_openid_999", "session_key": "newKey="}
    mock_redis = _make_mock_redis()

    with patch("auth.router.exchange_code_for_session", AsyncMock(return_value=mock_session)), \
         patch("auth.router.get_redis", AsyncMock(return_value=mock_redis)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/auth/login", json={"code": "wx_code_456"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["need_phone_binding"] is True
    assert "login_session" in data
    assert len(data["login_session"]) == 32  # uuid4().hex

    # Verify Redis was called with correct key prefix and TTL
    mock_redis.setex.assert_called_once()
    call_args = mock_redis.setex.call_args
    key = call_args[0][0]
    ttl = call_args[0][1]
    assert key.startswith("auth:session:")
    assert ttl == 600


# ---------------------------------------------------------------------------
# /bind_phone tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bind_phone_matches_writes_openid(db_session, override_db):
    """Valid session + phone matches IR user → writes openid to DB + returns token."""
    from models.ir_users import IRUser
    from sqlalchemy import select

    # Create IR user with a phone but no openid yet
    user = IRUser(name="待绑定IR", phone="13800138001", role="ir", is_active=True)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    stored = json.dumps({"openid": "fresh_openid_bind", "session_key": "sk_abc="})
    mock_redis = _make_mock_redis(stored_value=stored)

    # Mock decrypt_user_data to return phone without needing real AES
    with patch("auth.router.get_redis", AsyncMock(return_value=mock_redis)), \
         patch("auth.router.decrypt_user_data", return_value={"phoneNumber": "13800138001"}):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/auth/bind_phone", json={
                "login_session": "abcdef1234567890abcdef1234567890",
                "encryptedData": "fakeEncData",
                "iv": "fakeIv==",
            })

    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["name"] == "待绑定IR"

    # Verify openid was written to DB
    await db_session.refresh(user)
    assert user.wechat_openid == "fresh_openid_bind"

    # Verify Redis session was deleted
    mock_redis.delete.assert_called_once_with("auth:session:abcdef1234567890abcdef1234567890")


@pytest.mark.asyncio
async def test_bind_phone_no_match_returns_403(db_session, override_db):
    """Phone from WeChat doesn't match any IR user → 403."""
    stored = json.dumps({"openid": "some_openid_no_user", "session_key": "sk_xyz="})
    mock_redis = _make_mock_redis(stored_value=stored)

    with patch("auth.router.get_redis", AsyncMock(return_value=mock_redis)), \
         patch("auth.router.decrypt_user_data", return_value={"phoneNumber": "19900000000"}):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/auth/bind_phone", json={
                "login_session": "ffffffffffffffffffffffffffffffff",
                "encryptedData": "fakeEncData",
                "iv": "fakeIv==",
            })

    assert resp.status_code == 403
    assert "账号未开通" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_bind_phone_expired_session_returns_410(override_db):
    """login_session not in Redis (expired or invalid) → 410 Gone."""
    mock_redis = _make_mock_redis(stored_value=None)  # nothing stored

    with patch("auth.router.get_redis", AsyncMock(return_value=mock_redis)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/auth/bind_phone", json={
                "login_session": "expired_session_00000000000000000",
                "encryptedData": "fakeEncData",
                "iv": "fakeIv==",
            })

    assert resp.status_code == 410
    assert "expired" in resp.json()["detail"].lower()
