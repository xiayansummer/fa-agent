import pytest
import pytest_asyncio
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
from datetime import date, datetime, timedelta
from httpx import AsyncClient, ASGITransport
from main import app
from models.ir_users import IRUser
from auth.jwt import create_token

@pytest_asyncio.fixture(loop_scope="session")
async def ir_token(db_session):
    import uuid
    user = IRUser(name="日历测试IR", wechat_openid=f"cal_test_{uuid.uuid4().hex[:8]}")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return create_token(user.id, user.role)

@pytest.mark.asyncio
async def test_calendar_followup_event(db_session, ir_token, override_db):
    from models.investors import Investor
    old_date = datetime.combine(date.today() - timedelta(days=20), datetime.min.time())
    investor = Investor(
        name="久未联系人",
        agency="测试基金",
        last_interaction_at=old_date,
        is_active=True,
    )
    db_session.add(investor)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/calendar/daily",
            headers={"Authorization": f"Bearer {ir_token}"},
        )

    assert resp.status_code == 200
    events = resp.json()["events"]
    followup = [e for e in events if e["type"] == "followup"]
    assert any("久未联系人" in e["title"] for e in followup)

@pytest.mark.asyncio
async def test_calendar_birthday_event(db_session, ir_token, override_db):
    from models.investors import Investor
    investor = Investor(
        name="今日寿星",
        agency="寿星基金",
        birthday=date.today(),
        is_active=True,
    )
    db_session.add(investor)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/calendar/daily",
            headers={"Authorization": f"Bearer {ir_token}"},
        )

    events = resp.json()["events"]
    milestones = [e for e in events if e["type"] == "milestone"]
    assert any("今日寿星" in e["title"] for e in milestones)
