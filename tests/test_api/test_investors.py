import pytest
import pytest_asyncio
import sys, os, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
from httpx import AsyncClient, ASGITransport
from main import app
from models.ir_users import IRUser
from auth.jwt import create_token

@pytest_asyncio.fixture(loop_scope="session")
async def auth_headers(db_session):
    user = IRUser(name="测试IR", wechat_openid=f"inv_test_{uuid.uuid4().hex}")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    token = create_token(user.id, user.role)
    return {"Authorization": f"Bearer {token}"}

@pytest.mark.asyncio
async def test_list_investors_empty(auth_headers, override_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/investors", headers=auth_headers)
    assert resp.status_code == 200
    assert "items" in resp.json()

@pytest.mark.asyncio
async def test_create_and_get_investor(db_session, auth_headers, override_db):
    from models.investors import Investor
    investor = Investor(name="张伟", agency="高榕资本",
                        industry_tags=["消费"], stage_pref=["A轮"])
    db_session.add(investor)
    await db_session.commit()
    await db_session.refresh(investor)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/investors/{investor.id}", headers=auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "张伟"
    assert data["agency"] == "高榕资本"

@pytest.mark.asyncio
async def test_list_investors_with_filter(db_session, auth_headers, override_db):
    from models.investors import Investor
    db_session.add(Investor(name="FilterA", agency="X", stage_pref=["A轮"], is_active=True))
    db_session.add(Investor(name="FilterB", agency="Y", stage_pref=["B轮"], is_active=True))
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/investors?stage=A轮", headers=auth_headers)

    assert resp.status_code == 200
    names = [i["name"] for i in resp.json()["items"]]
    assert "FilterA" in names
    assert "FilterB" not in names
