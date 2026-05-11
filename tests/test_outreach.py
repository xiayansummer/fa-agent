"""Tests for outreach pending + history endpoints."""
import pytest
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from models.outreach_records import OutreachRecord
from models.ir_users import IRUser
from auth.jwt import create_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(ir_id: int, status: str, type: str = "meeting_minutes", channel: str = "wechat") -> OutreachRecord:
    return OutreachRecord(
        investor_id=1,
        ir_id=ir_id,
        type=type,
        channel=channel,
        content="test content",
        status=status,
    )


# ---------------------------------------------------------------------------
# GET /api/outreach/pending
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_pending_only_returns_drafts(authed_client, db_session):
    """Pending endpoint returns only draft records."""
    client, user = authed_client

    records = [
        _make_record(user.id, "draft"),
        _make_record(user.id, "approved"),
        _make_record(user.id, "sent"),
    ]
    for r in records:
        db_session.add(r)
    await db_session.commit()

    resp = await client.get("/api/outreach/pending")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    statuses = [item["status"] for item in items]
    assert all(s == "draft" for s in statuses), f"Got non-draft statuses: {statuses}"
    # At least the draft we created should be present
    assert any(item["status"] == "draft" for item in items)


@pytest.mark.asyncio(loop_scope="session")
async def test_pending_isolated_by_ir(authed_client, db_session):
    """Pending endpoint only returns current IR's records, not another IR's."""
    client, user = authed_client

    # Create another IR user
    other_user = IRUser(name="Other IR", phone="13900000099", role="ir", wechat_openid="other_openid_pending")
    db_session.add(other_user)
    await db_session.commit()
    await db_session.refresh(other_user)

    # Draft for current IR
    my_draft = _make_record(user.id, "draft")
    my_draft.content = "my draft content"
    # Draft for other IR
    other_draft = _make_record(other_user.id, "draft")
    other_draft.content = "other draft content"

    db_session.add(my_draft)
    db_session.add(other_draft)
    await db_session.commit()
    await db_session.refresh(my_draft)
    await db_session.refresh(other_draft)

    resp = await client.get("/api/outreach/pending")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    returned_ids = [item["id"] for item in items]

    assert my_draft.id in returned_ids, "Current IR's draft should appear"
    assert other_draft.id not in returned_ids, "Other IR's draft must not appear"

    # cleanup
    await db_session.delete(other_draft)
    await db_session.delete(other_user)
    await db_session.commit()


# ---------------------------------------------------------------------------
# GET /api/outreach/history
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_history_returns_all_statuses(authed_client, db_session):
    """History endpoint returns records of all statuses without filter."""
    client, user = authed_client

    # Create unique content markers so we can identify these records
    marker = "history_all_"
    records = [
        _make_record(user.id, "draft"),
        _make_record(user.id, "approved"),
        _make_record(user.id, "sent"),
    ]
    for i, r in enumerate(records):
        r.content = f"{marker}{i}"
        db_session.add(r)
    await db_session.commit()
    for r in records:
        await db_session.refresh(r)

    record_ids = {r.id for r in records}

    resp = await client.get("/api/outreach/history")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    returned_ids = {item["id"] for item in items}

    # All 3 records must be in the response
    assert record_ids.issubset(returned_ids), f"Missing records: {record_ids - returned_ids}"


@pytest.mark.asyncio(loop_scope="session")
async def test_history_filter_by_status(authed_client, db_session):
    """History with status=approved returns only approved records."""
    client, user = authed_client

    records = [
        _make_record(user.id, "draft"),
        _make_record(user.id, "approved"),
        _make_record(user.id, "sent"),
    ]
    for r in records:
        db_session.add(r)
    await db_session.commit()
    for r in records:
        await db_session.refresh(r)

    approved_id = records[1].id

    resp = await client.get("/api/outreach/history?status=approved")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    returned_ids = [item["id"] for item in items]

    assert approved_id in returned_ids, "Approved record should appear"
    # All returned items must be approved
    assert all(item["status"] == "approved" for item in items), \
        f"Non-approved records returned: {[item['status'] for item in items]}"


@pytest.mark.asyncio(loop_scope="session")
async def test_history_filter_by_type(authed_client, db_session):
    """History with type=meeting_minutes returns only that type."""
    client, user = authed_client

    types = ["meeting_minutes", "meeting_minutes", "industry_report", "daily_push"]
    records = []
    for t in types:
        r = _make_record(user.id, "sent", type=t)
        db_session.add(r)
        records.append(r)
    await db_session.commit()
    for r in records:
        await db_session.refresh(r)

    mm_ids = {r.id for r in records if r.type == "meeting_minutes"}

    resp = await client.get("/api/outreach/history?type=meeting_minutes")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    returned_ids = {item["id"] for item in items}

    assert mm_ids.issubset(returned_ids), f"Missing meeting_minutes records: {mm_ids - returned_ids}"
    assert all(item["type"] == "meeting_minutes" for item in items), \
        f"Non-meeting_minutes types returned"


@pytest.mark.asyncio(loop_scope="session")
async def test_history_pagination(authed_client, db_session):
    """History pagination: limit=2&offset=0 returns 2; offset=2 returns next 2."""
    client, user = authed_client

    # Create 5 records with unique content for easy identification
    page_records = []
    for i in range(5):
        r = OutreachRecord(
            investor_id=1,
            ir_id=user.id,
            type="daily_push",
            channel="wechat",
            content=f"pagination_test_{i}",
            status="sent",
        )
        db_session.add(r)
        page_records.append(r)
    await db_session.commit()
    for r in page_records:
        await db_session.refresh(r)

    # Page 1: limit=2, offset=0
    resp1 = await client.get("/api/outreach/history?limit=2&offset=0")
    assert resp1.status_code == 200, resp1.text
    page1 = resp1.json()
    assert len(page1) == 2, f"Expected 2 items, got {len(page1)}"

    # Page 2: limit=2, offset=2
    resp2 = await client.get("/api/outreach/history?limit=2&offset=2")
    assert resp2.status_code == 200, resp2.text
    page2 = resp2.json()
    assert len(page2) == 2, f"Expected 2 items, got {len(page2)}"

    # Pages must not overlap
    ids1 = {item["id"] for item in page1}
    ids2 = {item["id"] for item in page2}
    assert ids1.isdisjoint(ids2), f"Pages overlap: {ids1 & ids2}"


@pytest.mark.asyncio(loop_scope="session")
async def test_history_invalid_status(authed_client):
    """History with an invalid status value returns 422."""
    client, _ = authed_client
    resp = await client.get("/api/outreach/history?status=invalid_status")
    assert resp.status_code == 422, resp.text
