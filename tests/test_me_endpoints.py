import pytest
import os
import sys

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
