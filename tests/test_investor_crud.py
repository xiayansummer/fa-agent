"""Tests for investor CRUD endpoints (POST / PUT / DELETE)."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from models.investors import Investor
from sqlalchemy import select


# ---------------------------------------------------------------------------
# POST /api/investors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_create_investor(authed_client, db_session):
    client, _ = authed_client
    payload = {"name": "王芳"}
    resp = await client.post("/api/investors", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] == "王芳"
    assert "id" in data

    # verify in DB
    result = await db_session.execute(
        select(Investor).where(Investor.id == data["id"])
    )
    investor = result.scalar_one_or_none()
    assert investor is not None
    assert investor.name == "王芳"
    assert investor.is_active is True


@pytest.mark.asyncio(loop_scope="session")
async def test_create_investor_missing_name(authed_client):
    client, _ = authed_client
    resp = await client.post("/api/investors", json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PUT /api/investors/{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_update_investor_partial(authed_client, db_session):
    client, _ = authed_client
    # Create via API
    create_resp = await client.post(
        "/api/investors",
        json={"name": "李明", "agency": "原机构", "relationship_score": 3},
    )
    assert create_resp.status_code == 200
    investor_id = create_resp.json()["id"]

    # Partial update — only change agency
    update_resp = await client.put(
        f"/api/investors/{investor_id}",
        json={"agency": "新机构"},
    )
    assert update_resp.status_code == 200, update_resp.text
    data = update_resp.json()
    assert data["agency"] == "新机构"
    # Other fields should be unchanged
    assert data["name"] == "李明"
    assert data["relationship_score"] == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_update_investor_not_found(authed_client):
    client, _ = authed_client
    resp = await client.put("/api/investors/99999", json={"agency": "X"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/investors/{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_delete_investor_soft(authed_client, db_session):
    client, _ = authed_client
    # Create
    create_resp = await client.post("/api/investors", json={"name": "赵六"})
    assert create_resp.status_code == 200
    investor_id = create_resp.json()["id"]

    # Delete
    del_resp = await client.delete(f"/api/investors/{investor_id}")
    assert del_resp.status_code == 200, del_resp.text
    assert del_resp.json() == {"deleted": True}

    # DB: is_active should be False
    await db_session.refresh(
        (await db_session.execute(
            select(Investor).where(Investor.id == investor_id)
        )).scalar_one()
    )
    result = await db_session.execute(
        select(Investor).where(Investor.id == investor_id)
    )
    investor = result.scalar_one()
    assert investor.is_active is False

    # GET /api/investors should no longer return this investor
    list_resp = await client.get("/api/investors")
    assert list_resp.status_code == 200
    ids = [i["id"] for i in list_resp.json()["items"]]
    assert investor_id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_investor_not_found(authed_client):
    client, _ = authed_client
    resp = await client.delete("/api/investors/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_investor_already_inactive(authed_client, db_session):
    """DELETE on an already-inactive investor should return 404."""
    client, _ = authed_client
    # Insert directly as inactive
    investor = Investor(name="已删除投资人", is_active=False)
    db_session.add(investor)
    await db_session.commit()
    await db_session.refresh(investor)

    resp = await client.delete(f"/api/investors/{investor.id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST with dates and tags
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_create_with_dates_and_tags(authed_client, db_session):
    client, _ = authed_client
    payload = {
        "name": "陈七",
        "birthday": "1985-03-15",
        "industry_tags": ["医疗", "消费"],
        "stage_pref": ["A轮", "B轮"],
    }
    resp = await client.post("/api/investors", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] == "陈七"
    assert data["industry_tags"] == ["医疗", "消费"]
    assert data["stage_pref"] == ["A轮", "B轮"]

    # Verify dates stored in DB
    result = await db_session.execute(
        select(Investor).where(Investor.id == data["id"])
    )
    investor = result.scalar_one()
    assert str(investor.birthday) == "1985-03-15"
    assert investor.industry_tags == ["医疗", "消费"]
