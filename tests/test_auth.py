import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from main import app

@pytest.mark.asyncio
async def test_login_success(db_session, override_db):
    from models.ir_users import IRUser
    user = IRUser(name="测试IR", wechat_openid="test_openid_001", is_active=True)
    db_session.add(user)
    await db_session.commit()

    with patch("auth.router.exchange_code_for_openid", AsyncMock(return_value="test_openid_001")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/auth/login", json={"code": "fake_code"})

    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["name"] == "测试IR"
    assert data["role"] == "ir"

@pytest.mark.asyncio
async def test_login_unregistered(override_db):
    with patch("auth.router.exchange_code_for_openid", AsyncMock(return_value="unknown_openid_xyz")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/auth/login", json={"code": "fake_code"})

    assert resp.status_code == 403
    assert "账号未开通" in resp.json()["detail"]

@pytest.mark.asyncio
async def test_login_inactive_user(db_session, override_db):
    from models.ir_users import IRUser
    user = IRUser(name="离职IR", wechat_openid="inactive_openid_001", is_active=False)
    db_session.add(user)
    await db_session.commit()

    with patch("auth.router.exchange_code_for_openid", AsyncMock(return_value="inactive_openid_001")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/auth/login", json={"code": "fake_code"})

    assert resp.status_code == 403
