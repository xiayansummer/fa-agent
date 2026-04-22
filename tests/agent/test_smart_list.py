import pytest
import json
from langgraph.types import Command


@pytest.fixture
def base_state():
    return {
        "thread_id": "sl-test-001",
        "ir_id": 1,
        "task_type": "smart_list",
        "meeting_id": None,
        "audio_url": None,
        "transcript": None,
        "investor_ids": None,
        "investor_profiles": None,
        "target_date": None,
        "events": None,
        "criteria": "AI+SaaS方向，A轮，融资金额3000万人民币，团队有大厂背景",
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
async def test_smart_list_generates_ranked_draft(base_state, db_session, mocker):
    from agent.workflows.smart_list import smart_list_graph

    ranked = [
        {"investor_id": 1, "score": 90, "reason": "专注AI赛道，A轮活跃", "priority": "高"},
        {"investor_id": 2, "score": 70, "reason": "有SaaS经验", "priority": "中"},
    ]
    mocker.patch(
        "agent.workflows.smart_list.skill_registry.call",
        new=mocker.AsyncMock(return_value=json.dumps(ranked, ensure_ascii=False)),
    )
    mocker.patch(
        "agent.workflows.smart_list.AsyncSessionLocal",
        return_value=mocker.MagicMock(
            __aenter__=mocker.AsyncMock(return_value=db_session),
            __aexit__=mocker.AsyncMock(return_value=False),
        ),
    )

    config = {"configurable": {"thread_id": "sl-test-001"}}
    async for _ in smart_list_graph.astream(base_state, config, stream_mode="updates"):
        pass

    state = smart_list_graph.get_state(config)
    assert state.values["draft"] is not None
    assert len(state.tasks) > 0  # paused at interrupt


@pytest.mark.asyncio
async def test_smart_list_approve_saves_trace(base_state, db_session, mocker):
    from agent.workflows.smart_list import smart_list_graph

    ranked = [{"investor_id": 1, "score": 90, "reason": "匹配", "priority": "高"}]
    mocker.patch(
        "agent.workflows.smart_list.skill_registry.call",
        new=mocker.AsyncMock(return_value=json.dumps(ranked, ensure_ascii=False)),
    )
    mocker.patch(
        "agent.workflows.smart_list.AsyncSessionLocal",
        return_value=mocker.MagicMock(
            __aenter__=mocker.AsyncMock(return_value=db_session),
            __aexit__=mocker.AsyncMock(return_value=False),
        ),
    )
    base_state["thread_id"] = "sl-test-002"
    config = {"configurable": {"thread_id": "sl-test-002"}}

    async for _ in smart_list_graph.astream(base_state, config, stream_mode="updates"):
        pass
    async for _ in smart_list_graph.astream(
        Command(resume={"action": "approved", "final": json.dumps(ranked)}),
        config, stream_mode="updates"
    ):
        pass

    final = smart_list_graph.get_state(config).values
    assert final["ir_action"] == "approved"

    from sqlalchemy import select as sa_select
    from models.agent_traces import AgentTrace
    result = await db_session.execute(
        sa_select(AgentTrace).where(AgentTrace.thread_id == "sl-test-002")
    )
    trace = result.scalar_one_or_none()
    assert trace is not None
    assert trace.agent_name == "smart_list"
