import pytest
import json
from langgraph.types import Command


@pytest.fixture
def base_state():
    return {
        "thread_id": "dp-test-001",
        "ir_id": 1,
        "task_type": "daily_push",
        "meeting_id": None,
        "audio_url": None,
        "transcript": None,
        "investor_ids": [1, 2],
        "investor_profiles": None,
        "target_date": "2026-04-22",
        "events": None,
        "criteria": None,
        "candidate_ids": None,
        "investor_id": None,
        "milestone_type": None,
        "ir_name": None,
        "draft": None,
        "final": None,
        "ir_action": None,
        "prompt_version": None,
        "skills_called": [],
        "error": None,
    }


@pytest.mark.asyncio
async def test_daily_push_generates_draft(base_state, db_session, mocker):
    from agent.workflows.daily_push import daily_push_graph

    mock_messages = [
        {"investor_id": 1, "message": "张总好，最近有项目想和您分享"},
        {"investor_id": 2, "message": "李总好，关注到您最近的动态"},
    ]
    mocker.patch(
        "agent.workflows.daily_push.skill_registry.call",
        new=mocker.AsyncMock(return_value=json.dumps(mock_messages, ensure_ascii=False)),
    )
    mocker.patch(
        "agent.workflows.daily_push.AsyncSessionLocal",
        return_value=mocker.MagicMock(
            __aenter__=mocker.AsyncMock(return_value=db_session),
            __aexit__=mocker.AsyncMock(return_value=False),
        ),
    )

    config = {"configurable": {"thread_id": "dp-test-001"}}
    async for _ in daily_push_graph.astream(base_state, config, stream_mode="updates"):
        pass

    state = daily_push_graph.get_state(config)
    assert state.values["draft"] is not None
    assert len(state.tasks) > 0  # paused at interrupt


@pytest.mark.asyncio
async def test_daily_push_approve_and_save(base_state, db_session, mocker):
    from agent.workflows.daily_push import daily_push_graph

    mock_messages = [{"investor_id": 1, "message": "张总好"}]
    mocker.patch(
        "agent.workflows.daily_push.skill_registry.call",
        new=mocker.AsyncMock(return_value=json.dumps(mock_messages, ensure_ascii=False)),
    )
    mock_db = mocker.MagicMock(
        __aenter__=mocker.AsyncMock(return_value=db_session),
        __aexit__=mocker.AsyncMock(return_value=False),
    )
    mocker.patch("agent.workflows.daily_push.AsyncSessionLocal", return_value=mock_db)

    base_state["thread_id"] = "dp-test-002"
    config = {"configurable": {"thread_id": "dp-test-002"}}

    async for _ in daily_push_graph.astream(base_state, config, stream_mode="updates"):
        pass
    async for _ in daily_push_graph.astream(
        Command(resume={"action": "approved", "final": json.dumps(mock_messages)}),
        config, stream_mode="updates"
    ):
        pass

    final = daily_push_graph.get_state(config).values
    assert final["ir_action"] == "approved"
