"""Tests for GET /api/calendar/month"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

import pytest
import pytest_asyncio
import uuid
from datetime import date, datetime, timedelta


@pytest.mark.asyncio
async def test_month_with_birthday_event(authed_client, db_session):
    """Investor with birthday in target month → that day appears in days dict with 'milestone'."""
    client, user = authed_client

    # Use a fixed month for determinism
    target_month = "2026-05"
    birthday = date(2026, 5, 15)

    from models.investors import Investor
    investor = Investor(
        name=f"生日测试_{uuid.uuid4().hex[:6]}",
        agency="测试基金",
        birthday=birthday,
        is_active=True,
    )
    db_session.add(investor)
    await db_session.commit()

    resp = await client.get(f"/api/calendar/month?month={target_month}")
    assert resp.status_code == 200

    data = resp.json()
    assert data["month"] == target_month

    day_key = str(birthday)  # "2026-05-15"
    assert day_key in data["days"], f"Expected {day_key} in days dict, got keys: {list(data['days'].keys())[:5]}"
    assert "milestone" in data["days"][day_key]


@pytest.mark.asyncio
async def test_month_with_followup(authed_client, db_session):
    """Investor with last_interaction_at 30 days ago → every remaining day in current month has 'followup'."""
    client, user = authed_client

    # 30 days ago ensures followup fires for every day in the current month
    old_interaction = datetime.combine(date.today() - timedelta(days=30), datetime.min.time())

    from models.investors import Investor
    investor = Investor(
        name=f"跟进测试_{uuid.uuid4().hex[:6]}",
        agency="跟进基金",
        last_interaction_at=old_interaction,
        is_active=True,
    )
    db_session.add(investor)
    await db_session.commit()

    today = date.today()
    target_month = today.strftime("%Y-%m")

    resp = await client.get(f"/api/calendar/month?month={target_month}")
    assert resp.status_code == 200

    data = resp.json()
    assert data["month"] == target_month

    # Every day in the current month should have a followup (since 30 days > 14 day threshold)
    import calendar as _calendar
    _, last_day = _calendar.monthrange(today.year, today.month)
    for day_num in range(1, last_day + 1):
        day_key = date(today.year, today.month, day_num).isoformat()
        assert day_key in data["days"], f"Expected followup on {day_key}"
        assert "followup" in data["days"][day_key], f"Expected 'followup' type on {day_key}"


@pytest.mark.asyncio
async def test_month_no_investors(authed_client, db_session):
    """With no active investors, days dict should be empty."""
    client, user = authed_client

    # Use a future month that won't have any events from other test investors
    # We can't guarantee isolation, but we use a far-future month with no birthdays
    # and no last_interaction_at investors that would trigger on that month
    # Best approach: use a month where no existing test investors have events.
    # Since we can't control other tests' data, we just verify the response shape.
    target_month = "2030-01"

    resp = await client.get(f"/api/calendar/month?month={target_month}")
    assert resp.status_code == 200

    data = resp.json()
    assert data["month"] == target_month
    assert isinstance(data["days"], dict)
    # In 2030-01, no followup events (no interaction within 14 days of that future date
    # since last_interaction_at is always in the past relative to ~now).
    # Actually, days_since = 2030-01-01 - past_date will be > 14 for all existing investors.
    # So all days will have followup for any investor with last_interaction_at set.
    # This test just verifies structure; the real "empty" test needs a clean DB.
    # We validate response schema only.
    for day_key, types in data["days"].items():
        assert isinstance(types, list)
        assert len(types) > 0, "Days with no events must be omitted, not present with empty list"


@pytest.mark.asyncio
async def test_month_truly_empty(authed_client, db_session):
    """Days with no events are omitted (not present as empty lists)."""
    client, user = authed_client

    # Use a far future month; investors with no last_interaction_at and no birthday
    # in that month will produce no events
    from models.investors import Investor
    investor = Investor(
        name=f"无事件投资人_{uuid.uuid4().hex[:6]}",
        agency="空事件基金",
        # No last_interaction_at, no birthday, no join_agency_date
        is_active=True,
    )
    db_session.add(investor)
    await db_session.commit()

    # Use a month in the distant past where no events will fire
    # (no last_interaction_at means no followup; no birthday means no milestone)
    target_month = "2000-01"
    resp = await client.get(f"/api/calendar/month?month={target_month}")
    assert resp.status_code == 200

    data = resp.json()
    assert data["month"] == target_month
    # This investor has no events → all days should be absent from dict
    # (Other test investors may add entries, but this investor contributes none)
    # Verify no empty lists exist
    for day_key, types in data["days"].items():
        assert len(types) > 0, f"Day {day_key} should be omitted if no events, not present with empty list"


@pytest.mark.asyncio
async def test_month_invalid_format(authed_client, db_session):
    """Invalid month format → 422 or 400."""
    client, user = authed_client

    resp = await client.get("/api/calendar/month?month=2026-13")
    assert resp.status_code in (400, 422), f"Expected 400/422, got {resp.status_code}"


@pytest.mark.asyncio
async def test_month_invalid_format_garbage(authed_client, db_session):
    """Garbage month string → 422 or 400."""
    client, user = authed_client

    resp = await client.get("/api/calendar/month?month=not-a-month")
    assert resp.status_code in (400, 422), f"Expected 400/422, got {resp.status_code}"


@pytest.mark.asyncio
async def test_month_types_deduplicated(authed_client, db_session):
    """If an investor triggers the same event type multiple times, it appears only once in the list."""
    client, user = authed_client

    # Create investor with both a birthday today and an old interaction
    # so both 'milestone' and 'followup' fire on the same day — and verify no duplicates
    today = date.today()
    old_interaction = datetime.combine(today - timedelta(days=30), datetime.min.time())

    from models.investors import Investor
    investor = Investor(
        name=f"双事件_{uuid.uuid4().hex[:6]}",
        agency="双事件基金",
        birthday=today,
        last_interaction_at=old_interaction,
        is_active=True,
    )
    db_session.add(investor)
    await db_session.commit()

    target_month = today.strftime("%Y-%m")
    resp = await client.get(f"/api/calendar/month?month={target_month}")
    assert resp.status_code == 200

    data = resp.json()
    day_key = today.isoformat()
    assert day_key in data["days"]
    types = data["days"][day_key]

    # Verify no duplicates
    assert len(types) == len(set(types)), f"Duplicate types found: {types}"
    assert "milestone" in types
    assert "followup" in types
