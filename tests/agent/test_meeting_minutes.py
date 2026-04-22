import pytest
from langgraph.types import Command


@pytest.fixture
def base_state():
    return {
        "thread_id": "mm-test-001",
        "ir_id": 1,
        "task_type": "meeting_minutes",
        "meeting_id": None,
        "audio_url": None,
        "transcript": "张总：我们对AI赛道很感兴趣，偏好A轮，单笔投资500万到2000万。",
        "investor_ids": [1],
        "investor_profiles": None,
        "target_date": None,
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
async def test_meeting_minutes_generates_draft(base_state, db_session, mocker):
    from agent.workflows.meeting_minutes import meeting_minutes_graph

    mocker.patch(
        "agent.workflows.meeting_minutes.skill_registry.call",
        new=mocker.AsyncMock(return_value="生成的会议纪要内容"),
    )
    mocker.patch(
        "agent.workflows.meeting_minutes.AsyncSessionLocal",
        return_value=mocker.MagicMock(
            __aenter__=mocker.AsyncMock(return_value=db_session),
            __aexit__=mocker.AsyncMock(return_value=False),
        ),
    )

    config = {"configurable": {"thread_id": "mm-test-001"}}

    events = []
    async for event in meeting_minutes_graph.astream(base_state, config, stream_mode="updates"):
        events.append(event)

    state = meeting_minutes_graph.get_state(config)
    assert state.values["draft"] is not None
    assert state.values["draft"] == "生成的会议纪要内容"
    # Should be paused at interrupt
    assert len(state.tasks) > 0


@pytest.mark.asyncio
async def test_meeting_minutes_approve_saves(base_state, db_session, mocker):
    from agent.workflows.meeting_minutes import meeting_minutes_graph

    mocker.patch(
        "agent.workflows.meeting_minutes.skill_registry.call",
        new=mocker.AsyncMock(return_value="生成的会议纪要内容"),
    )
    mock_db_ctx = mocker.MagicMock(
        __aenter__=mocker.AsyncMock(return_value=db_session),
        __aexit__=mocker.AsyncMock(return_value=False),
    )
    mocker.patch("agent.workflows.meeting_minutes.AsyncSessionLocal", return_value=mock_db_ctx)

    config = {"configurable": {"thread_id": "mm-test-002"}}
    base_state["thread_id"] = "mm-test-002"

    async for _ in meeting_minutes_graph.astream(base_state, config, stream_mode="updates"):
        pass

    async for _ in meeting_minutes_graph.astream(
        Command(resume={"action": "approved", "final": "最终纪要内容"}),
        config, stream_mode="updates"
    ):
        pass

    final = meeting_minutes_graph.get_state(config).values
    assert final["ir_action"] == "approved"
    assert final["final"] == "最终纪要内容"
