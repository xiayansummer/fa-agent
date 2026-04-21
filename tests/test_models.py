import pytest
from models.investors import Investor
from models.ir_users import IRUser

@pytest.mark.asyncio
async def test_create_investor(db_session):
    investor = Investor(name="张伟", agency="高榕资本", industry_tags=["消费", "TMT"])
    db_session.add(investor)
    await db_session.commit()
    await db_session.refresh(investor)
    assert investor.id is not None
    assert investor.relationship_score == 0

@pytest.mark.asyncio
async def test_create_ir_user(db_session):
    user = IRUser(name="李IR", phone="13800000000")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    assert user.id is not None
    assert user.role == "ir"
    assert user.is_active is True
