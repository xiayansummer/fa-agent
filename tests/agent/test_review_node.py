import pytest
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command


def _make_test_graph(review_node_func):
    """Build a minimal graph: START → set_draft → review → END"""
    from agent.state import AgentState

    def set_draft(state):
        return {"draft": "会议纪要草稿内容"}

    builder = StateGraph(AgentState)
    builder.add_node("set_draft", set_draft)
    builder.add_node("review", review_node_func)
    builder.add_edge(START, "set_draft")
    builder.add_edge("set_draft", "review")
    builder.add_edge("review", END)
    return builder.compile(checkpointer=MemorySaver())


@pytest.mark.asyncio
async def test_review_node_interrupt():
    from agent.nodes.review_node import review_node
    graph = _make_test_graph(review_node)
    config = {"configurable": {"thread_id": "test-review-001"}}

    initial = {
        "thread_id": "test-review-001",
        "ir_id": 1,
        "task_type": "meeting_minutes",
        "meeting_id": None, "audio_url": None, "transcript": None,
        "investor_ids": None, "investor_profiles": None,
        "target_date": None, "events": None,
        "criteria": None, "candidate_ids": None,
        "investor_id": None, "milestone_type": None, "ir_name": None,
        "draft": None, "final": None, "ir_action": None,
        "prompt_version": None, "skills_called": [], "error": None,
    }

    events = []
    async for event in graph.astream(initial, config, stream_mode="updates"):
        events.append(event)

    # Graph should have paused at interrupt
    state = graph.get_state(config)
    assert len(state.tasks) > 0  # interrupt leaves a pending task
    interrupt_val = state.tasks[0].interrupts[0].value
    assert interrupt_val["draft"] == "会议纪要草稿内容"
    assert interrupt_val["task_type"] == "meeting_minutes"


@pytest.mark.asyncio
async def test_review_node_resume_approved():
    from agent.nodes.review_node import review_node
    graph = _make_test_graph(review_node)
    config = {"configurable": {"thread_id": "test-review-002"}}

    initial = {
        "thread_id": "test-review-002",
        "ir_id": 1,
        "task_type": "meeting_minutes",
        "meeting_id": None, "audio_url": None, "transcript": None,
        "investor_ids": None, "investor_profiles": None,
        "target_date": None, "events": None,
        "criteria": None, "candidate_ids": None,
        "investor_id": None, "milestone_type": None, "ir_name": None,
        "draft": None, "final": None, "ir_action": None,
        "prompt_version": None, "skills_called": [], "error": None,
    }

    async for _ in graph.astream(initial, config, stream_mode="updates"):
        pass

    # Resume with approval
    async for _ in graph.astream(
        Command(resume={"action": "approved", "final": "会议纪要草稿内容"}),
        config, stream_mode="updates"
    ):
        pass

    final_state = graph.get_state(config).values
    assert final_state["ir_action"] == "approved"
    assert final_state["final"] == "会议纪要草稿内容"


@pytest.mark.asyncio
async def test_review_node_resume_modified():
    from agent.nodes.review_node import review_node
    graph = _make_test_graph(review_node)
    config = {"configurable": {"thread_id": "test-review-003"}}

    initial = {
        "thread_id": "test-review-003",
        "ir_id": 1,
        "task_type": "meeting_minutes",
        "meeting_id": None, "audio_url": None, "transcript": None,
        "investor_ids": None, "investor_profiles": None,
        "target_date": None, "events": None,
        "criteria": None, "candidate_ids": None,
        "investor_id": None, "milestone_type": None, "ir_name": None,
        "draft": None, "final": None, "ir_action": None,
        "prompt_version": None, "skills_called": [], "error": None,
    }

    async for _ in graph.astream(initial, config, stream_mode="updates"):
        pass

    async for _ in graph.astream(
        Command(resume={"action": "modified", "final": "IR修改后的内容"}),
        config, stream_mode="updates"
    ):
        pass

    final_state = graph.get_state(config).values
    assert final_state["ir_action"] == "modified"
    assert final_state["final"] == "IR修改后的内容"
