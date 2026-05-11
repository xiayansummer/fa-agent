import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from main import app

_MOCK_SESSION = {"openid": "test_openid_001", "session_key": "dummyKey="}


@pytest.mark.asyncio
async def test_login_success(db_session, override_db):
    from models.ir_users import IRUser
    user = IRUser(name="测试IR", wechat_openid="test_openid_001", is_active=True)
    db_session.add(user)
    await db_session.commit()

    with patch("auth.router.exchange_code_for_session", AsyncMock(return_value=_MOCK_SESSION)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/auth/login", json={"code": "fake_code"})

    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["name"] == "测试IR"
    assert data["role"] == "ir"


@pytest.mark.asyncio
async def test_login_unregistered_returns_need_binding(override_db):
    """Unbound openid → 200 with need_phone_binding=True (new flow)."""
    new_session = {"openid": "unknown_openid_xyz", "session_key": "dummyKey="}
    with patch("auth.router.exchange_code_for_session", AsyncMock(return_value=new_session)), \
         patch("auth.router.get_redis") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()
        mock_get_redis.return_value = mock_redis

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/auth/login", json={"code": "fake_code"})

    assert resp.status_code == 200
    data = resp.json()
    assert data.get("need_phone_binding") is True
    assert "login_session" in data


@pytest.mark.asyncio
async def test_login_inactive_user_returns_need_binding(db_session, override_db):
    """Inactive user's openid is already bound but is_active=False → treated as unbound → need_phone_binding."""
    from models.ir_users import IRUser
    user = IRUser(name="离职IR", wechat_openid="inactive_openid_001", is_active=False)
    db_session.add(user)
    await db_session.commit()

    session = {"openid": "inactive_openid_001", "session_key": "dummyKey="}
    with patch("auth.router.exchange_code_for_session", AsyncMock(return_value=session)), \
         patch("auth.router.get_redis") as mock_get_redis:
        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()
        mock_get_redis.return_value = mock_redis

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/auth/login", json={"code": "fake_code"})

    assert resp.status_code == 200
    data = resp.json()
    assert data.get("need_phone_binding") is True
