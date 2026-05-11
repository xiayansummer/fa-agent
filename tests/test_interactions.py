"""Tests for interaction log endpoints."""
import pytest
import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from models.investors import Investor
from models.interaction_logs import InteractionLog
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# POST /{investor_id}/interactions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_create_interaction_updates_last_interaction_at(authed_client, db_session):
    client, _ = authed_client

    investor = Investor(name="互动测试投资人A", is_active=True)
    db_session.add(investor)
    await db_session.commit()
    await db_session.refresh(investor)

    now = _now_utc()
    payload = {
        "type": "meeting",
        "occurred_at": now.isoformat(),
        "summary": "初次见面",
    }
    resp = await client.post(f"/api/investors/{investor.id}/interactions", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["investor_id"] == investor.id
    assert data["type"] == "meeting"
    assert data["summary"] == "初次见面"
    assert data["agent_generated"] is False

    # Verify investor.last_interaction_at was updated in DB
    await db_session.refresh(investor)
    assert investor.last_interaction_at is not None
    # The stored value should be close to now (within 2 seconds)
    diff = abs((investor.last_interaction_at - now).total_seconds())
    assert diff < 2, f"last_interaction_at={investor.last_interaction_at}, now={now}, diff={diff}"


@pytest.mark.asyncio(loop_scope="session")
async def test_create_interaction_old_doesnt_overwrite_newer_last(authed_client, db_session):
    client, _ = authed_client

    today = _now_utc()
    investor = Investor(name="互动测试投资人B", is_active=True, last_interaction_at=today)
    db_session.add(investor)
    await db_session.commit()
    await db_session.refresh(investor)

    yesterday = today - timedelta(days=1)
    payload = {
        "type": "email",
        "occurred_at": yesterday.isoformat(),
        "summary": "昨天的邮件",
    }
    resp = await client.post(f"/api/investors/{investor.id}/interactions", json=payload)
    assert resp.status_code == 200, resp.text

    # last_interaction_at should remain today (not overwritten by yesterday)
    await db_session.refresh(investor)
    assert investor.last_interaction_at is not None
    diff = abs((investor.last_interaction_at - today).total_seconds())
    assert diff < 2, f"last_interaction_at should not have changed; got {investor.last_interaction_at}"


@pytest.mark.asyncio(loop_scope="session")
async def test_create_interaction_investor_not_found(authed_client):
    client, _ = authed_client
    payload = {
        "type": "call",
        "occurred_at": _now_utc().isoformat(),
        "summary": "不存在的投资人",
    }
    resp = await client.post("/api/investors/99999/interactions", json=payload)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /{investor_id}/interactions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_list_interactions_time_desc(authed_client, db_session):
    client, user = authed_client

    investor = Investor(name="互动测试投资人C", is_active=True)
    db_session.add(investor)
    await db_session.commit()
    await db_session.refresh(investor)

    base = _now_utc()
    for i, hours_ago in enumerate([10, 5, 1]):
        log = InteractionLog(
            investor_id=investor.id,
            ir_id=user.id,
            type="meeting",
            occurred_at=base - timedelta(hours=hours_ago),
            summary=f"第{i+1}次会议",
            agent_generated=False,
        )
        db_session.add(log)
    await db_session.commit()

    resp = await client.get(f"/api/investors/{investor.id}/interactions")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 3
    # Verify descending order by occurred_at
    times = [item["occurred_at"] for item in items]
    assert times == sorted(times, reverse=True), f"Expected descending order, got: {times}"


@pytest.mark.asyncio(loop_scope="session")
async def test_list_interactions_limit(authed_client, db_session):
    client, user = authed_client

    investor = Investor(name="互动测试投资人D", is_active=True)
    db_session.add(investor)
    await db_session.commit()
    await db_session.refresh(investor)

    base = _now_utc()
    for i in range(10):
        log = InteractionLog(
            investor_id=investor.id,
            ir_id=user.id,
            type="wechat",
            occurred_at=base - timedelta(hours=i),
            summary=f"微信沟通{i+1}",
            agent_generated=False,
        )
        db_session.add(log)
    await db_session.commit()

    resp = await client.get(f"/api/investors/{investor.id}/interactions?limit=3")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_list_interactions_invalid_limit(authed_client, db_session):
    client, _ = authed_client

    investor = Investor(name="互动测试投资人E", is_active=True)
    db_session.add(investor)
    await db_session.commit()
    await db_session.refresh(investor)

    resp = await client.get(f"/api/investors/{investor.id}/interactions?limit=999")
    assert resp.status_code == 422
