import pytest
from langgraph.types import Command


@pytest.fixture
def base_state():
    return {
        "thread_id": "mo-test-001",
        "ir_id": 1,
        "task_type": "milestone_outreach",
        "meeting_id": None,
        "audio_url": None,
        "transcript": None,
        "investor_ids": None,
        "investor_profiles": None,
        "target_date": None,
        "events": None,
        "criteria": None,
        "candidate_ids": None,
        "investor_id": 1,
        "milestone_type": "birthday",
        "ir_name": "王IR",
        "draft": None,
        "final": None,
        "ir_action": None,
        "prompt_version": None,
        "skills_called": [],
        "error": None,
    }


@pytest.mark.asyncio
async def test_milestone_outreach_generates_message(base_state, db_session, mocker):
    from agent.workflows.milestone_outreach import milestone_outreach_graph
    from models.investors import Investor

    db_session.add(Investor(id=1, name="张三", agency="红杉资本", is_active=True))
    await db_session.commit()

    mocker.patch(
        "agent.workflows.milestone_outreach.skill_registry.call",
        new=mocker.AsyncMock(return_value="张总，今天是您的生日，祝您生日快乐！"),
    )
    mocker.patch(
        "agent.workflows.milestone_outreach.AsyncSessionLocal",
        return_value=mocker.MagicMock(
            __aenter__=mocker.AsyncMock(return_value=db_session),
            __aexit__=mocker.AsyncMock(return_value=False),
        ),
    )

    config = {"configurable": {"thread_id": "mo-test-001"}}
    async for _ in milestone_outreach_graph.astream(base_state, config, stream_mode="updates"):
        pass

    state = milestone_outreach_graph.get_state(config)
    assert state.values["draft"] is not None
    assert len(state.tasks) > 0  # paused at interrupt


@pytest.mark.asyncio
async def test_milestone_outreach_approve_saves(base_state, db_session, mocker):
    from agent.workflows.milestone_outreach import milestone_outreach_graph

    mocker.patch(
        "agent.workflows.milestone_outreach.skill_registry.call",
        new=mocker.AsyncMock(return_value="生日祝福消息"),
    )
    mocker.patch(
        "agent.workflows.milestone_outreach.AsyncSessionLocal",
        return_value=mocker.MagicMock(
            __aenter__=mocker.AsyncMock(return_value=db_session),
            __aexit__=mocker.AsyncMock(return_value=False),
        ),
    )
    base_state["thread_id"] = "mo-test-002"
    config = {"configurable": {"thread_id": "mo-test-002"}}

    async for _ in milestone_outreach_graph.astream(base_state, config, stream_mode="updates"):
        pass
    async for _ in milestone_outreach_graph.astream(
        Command(resume={"action": "approved", "final": "最终生日祝福"}),
        config, stream_mode="updates"
    ):
        pass

    final = milestone_outreach_graph.get_state(config).values
    assert final["ir_action"] == "approved"
    assert final["final"] == "最终生日祝福"

    from sqlalchemy import select as sa_select
    from models.agent_traces import AgentTrace
    result = await db_session.execute(
        sa_select(AgentTrace).where(AgentTrace.thread_id == "mo-test-002")
    )
    trace = result.scalar_one_or_none()
    assert trace is not None
    assert trace.agent_name == "milestone_outreach"
