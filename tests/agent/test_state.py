import pytest
import json
from agent.state import AgentState

def test_agent_state_shape():
    state: AgentState = {
        "thread_id": "t1",
        "ir_id": 1,
        "task_type": "meeting_minutes",
        "meeting_id": None,
        "audio_url": None,
        "transcript": "hello",
        "investor_ids": [1, 2],
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
    assert state["thread_id"] == "t1"
    assert state["skills_called"] == []


@pytest.mark.asyncio
async def test_events_publish_subscribe(mocker):
    from agent.events import publish, subscribe

    # Mock Redis connection
    mock_redis = mocker.AsyncMock()
    mock_redis.publish = mocker.AsyncMock()
    mocker.patch("agent.events.get_redis", new=mocker.AsyncMock(return_value=mock_redis))

    await publish("thread-1", {"type": "node_done", "node": "transcribe"})
    mock_redis.publish.assert_called_once_with(
        "agent:events:thread-1",
        json.dumps({"type": "node_done", "node": "transcribe"}),
    )
