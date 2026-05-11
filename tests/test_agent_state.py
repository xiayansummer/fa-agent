"""Tests for GET /api/agent/{thread_id}/state endpoint."""
import pytest
from unittest.mock import MagicMock
from httpx import AsyncClient, ASGITransport
from auth.jwt import create_token


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {create_token(ir_id=1, role='ir')}"}


def _make_mock_redis(mocker, owner=None, task_type=None):
    """Helper: build a mock Redis where get() returns owner then task_type on successive calls."""
    mock_redis = mocker.AsyncMock()

    async def _get(key):
        if ":owner" in key:
            return owner
        if ":type" in key:
            return task_type
        return None

    mock_redis.get = _get
    return mock_redis


# ---------------------------------------------------------------------------
# test_state_thread_not_found
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_state_thread_not_found(override_db, mocker, auth_headers):
    from main import app

    mock_redis = _make_mock_redis(mocker, owner=None, task_type=None)
    mocker.patch("api.agent.get_redis", new=mocker.AsyncMock(return_value=mock_redis))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/agent/nonexistent-thread/state",
            headers=auth_headers,
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# test_state_wrong_owner
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_state_wrong_owner(override_db, mocker, auth_headers):
    """Owner in Redis is a different ir_id → 403."""
    from main import app

    # auth_headers use ir_id=1; Redis owner is 999
    mock_redis = _make_mock_redis(mocker, owner="999", task_type="daily_push")
    mocker.patch("api.agent.get_redis", new=mocker.AsyncMock(return_value=mock_redis))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/agent/some-thread/state",
            headers=auth_headers,
        )

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# test_state_running
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_state_running(override_db, mocker, auth_headers):
    """Graph has non-empty next, no draft → status=running."""
    from main import app

    mock_redis = _make_mock_redis(mocker, owner="1", task_type="daily_push")
    mocker.patch("api.agent.get_redis", new=mocker.AsyncMock(return_value=mock_redis))

    mock_state = MagicMock()
    mock_state.next = ("some_node",)
    mock_state.values = {}

    mock_graph = MagicMock()
    mock_graph.get_state.return_value = mock_state

    mocker.patch("api.agent.get_graph", return_value=mock_graph)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/agent/thread-running/state",
            headers=auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"


# ---------------------------------------------------------------------------
# test_state_waiting_review
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_state_waiting_review(override_db, mocker, auth_headers):
    """Graph next includes 'review' and draft is present → status=waiting_review."""
    from main import app

    mock_redis = _make_mock_redis(mocker, owner="1", task_type="daily_push")
    mocker.patch("api.agent.get_redis", new=mocker.AsyncMock(return_value=mock_redis))

    mock_state = MagicMock()
    mock_state.next = ("review",)
    mock_state.values = {"draft": "test draft content"}

    mock_graph = MagicMock()
    mock_graph.get_state.return_value = mock_state

    mocker.patch("api.agent.get_graph", return_value=mock_graph)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/agent/thread-review/state",
            headers=auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "waiting_review"
    assert data["draft"] == "test draft content"
    assert data["task_type"] == "daily_push"


# ---------------------------------------------------------------------------
# test_state_done
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_state_done(override_db, mocker, auth_headers):
    """Graph next is empty → status=done with final and ir_action."""
    from main import app

    mock_redis = _make_mock_redis(mocker, owner="1", task_type="daily_push")
    mocker.patch("api.agent.get_redis", new=mocker.AsyncMock(return_value=mock_redis))

    mock_state = MagicMock()
    mock_state.next = ()
    mock_state.values = {"final": "Y", "ir_action": "approve"}

    mock_graph = MagicMock()
    mock_graph.get_state.return_value = mock_state

    mocker.patch("api.agent.get_graph", return_value=mock_graph)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/agent/thread-done/state",
            headers=auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "done"
    assert data["final"] == "Y"
    assert data["ir_action"] == "approve"


# ---------------------------------------------------------------------------
# test_state_error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_state_error(override_db, mocker, auth_headers):
    """Graph state has error field set → status=error."""
    from main import app

    mock_redis = _make_mock_redis(mocker, owner="1", task_type="daily_push")
    mocker.patch("api.agent.get_redis", new=mocker.AsyncMock(return_value=mock_redis))

    mock_state = MagicMock()
    mock_state.next = ("some_node",)
    mock_state.values = {"error": "boom"}

    mock_graph = MagicMock()
    mock_graph.get_state.return_value = mock_state

    mocker.patch("api.agent.get_graph", return_value=mock_graph)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/agent/thread-error/state",
            headers=auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "error"
    assert data["error"] == "boom"
