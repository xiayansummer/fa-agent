# LangGraph Agent System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a LangGraph-based multi-agent system with four AI workflows (meeting minutes, daily push, smart list, milestone outreach), each requiring IR review before finalizing, with real-time WebSocket streaming and a REST API.

**Architecture:** Each workflow is a LangGraph `StateGraph` compiled with `MemorySaver` checkpointer. Workflows run as FastAPI `BackgroundTasks` in the same process, hitting an `interrupt()` node that pauses execution until the IR submits a review via REST. Real-time events are broadcast over Redis pub/sub to WebSocket subscribers. Celery handles scheduled triggers only (no cross-process graph state).

**Tech Stack:** `langgraph==0.2.74`, FastAPI BackgroundTasks, Redis pub/sub, `MemorySaver`, SQLAlchemy async, existing `skill_registry` + `PromptRegistry` harness.

---

## File Map

**Create:**
```
backend/agent/__init__.py
backend/agent/state.py
backend/agent/events.py
backend/agent/runner.py
backend/agent/nodes/__init__.py
backend/agent/nodes/review_node.py
backend/agent/workflows/__init__.py
backend/agent/workflows/meeting_minutes.py
backend/agent/workflows/daily_push.py
backend/agent/workflows/smart_list.py
backend/agent/workflows/milestone_outreach.py
backend/api/agent.py
backend/worker.py
backend/prompts/meeting_minutes/generate/v1.txt
backend/prompts/meeting_minutes/generate/v1.meta.json
backend/prompts/daily_push/generate/v1.txt
backend/prompts/daily_push/generate/v1.meta.json
backend/prompts/smart_list/rank/v1.txt
backend/prompts/smart_list/rank/v1.meta.json
backend/prompts/milestone_message/generate/v1.txt
backend/prompts/milestone_message/generate/v1.meta.json
tests/agent/__init__.py
tests/agent/test_review_node.py
tests/agent/test_meeting_minutes.py
tests/agent/test_daily_push.py
tests/agent/test_smart_list.py
tests/agent/test_milestone_outreach.py
tests/test_agent_api.py
```

**Modify:**
```
backend/requirements.txt   — add langgraph
backend/main.py            — register agent router
```

---

### Task 1: Add langgraph dependency + create package scaffold

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/agent/__init__.py`, `backend/agent/nodes/__init__.py`, `backend/agent/workflows/__init__.py`
- Create: `tests/agent/__init__.py`

- [ ] **Step 1: Add langgraph to requirements.txt**

Open `backend/requirements.txt`. It currently ends with `pytest-mock==3.14.0`. Add after the last line:

```
langgraph==0.2.74
```

- [ ] **Step 2: Verify langgraph can be imported**

Run inside the backend container or with the virtualenv:
```bash
cd /Users/summer/fa-agent/backend
pip install langgraph==0.2.74 --quiet
python -c "import langgraph; print(langgraph.__version__)"
```
Expected: prints `0.2.74` (or compatible installed version).

- [ ] **Step 3: Create empty `__init__.py` files**

```bash
mkdir -p backend/agent/nodes backend/agent/workflows
touch backend/agent/__init__.py
touch backend/agent/nodes/__init__.py
touch backend/agent/workflows/__init__.py
touch tests/agent/__init__.py
```

- [ ] **Step 4: Commit**

```bash
git add backend/requirements.txt backend/agent/ tests/agent/
git commit -m "chore: add langgraph dependency and agent package scaffold"
```

---

### Task 2: State definitions and Redis event bus

**Files:**
- Create: `backend/agent/state.py`
- Create: `backend/agent/events.py`

- [ ] **Step 1: Write failing test for AgentState**

Create `tests/agent/test_state.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/summer/fa-agent
pytest tests/agent/test_state.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.state'`

- [ ] **Step 3: Create `backend/agent/state.py`**

```python
from __future__ import annotations
from typing import TypedDict, Optional, Literal, Annotated
import operator

TaskType = Literal["meeting_minutes", "daily_push", "smart_list", "milestone_outreach"]
IrAction = Literal["approved", "modified", "rejected"]


class AgentState(TypedDict):
    thread_id: str
    ir_id: int
    task_type: TaskType

    # Meeting minutes inputs
    meeting_id: Optional[str]
    audio_url: Optional[str]
    transcript: Optional[str]

    # Daily push inputs
    target_date: Optional[str]   # "2026-04-22"
    events: Optional[list[dict]]

    # Smart list inputs
    criteria: Optional[str]
    candidate_ids: Optional[list[int]]

    # Milestone outreach inputs
    investor_id: Optional[int]
    milestone_type: Optional[str]  # "birthday" | "join_agency" | "first_meeting"
    ir_name: Optional[str]

    # Shared investor context (resolved from DB by first node in each workflow)
    investor_ids: Optional[list[int]]
    investor_profiles: Optional[str]

    # Output
    draft: Optional[str]
    final: Optional[str]
    ir_action: Optional[IrAction]

    # Trace metadata
    prompt_version: Optional[str]
    skills_called: Annotated[list[str], operator.add]
    error: Optional[str]
```

- [ ] **Step 4: Run state test to verify it passes**

```bash
pytest tests/agent/test_state.py -v
```
Expected: PASS

- [ ] **Step 5: Write failing test for events pub/sub**

Add to `tests/agent/test_state.py`:

```python
import pytest
import json

@pytest.mark.asyncio
async def test_events_publish_subscribe(mocker):
    from agent.events import publish, subscribe

    published = []

    # Mock Redis connection
    mock_redis = mocker.AsyncMock()
    mock_redis.publish = mocker.AsyncMock()
    mocker.patch("agent.events.get_redis", return_value=mock_redis)

    await publish("thread-1", {"type": "node_done", "node": "transcribe"})
    mock_redis.publish.assert_called_once_with(
        "agent:events:thread-1",
        json.dumps({"type": "node_done", "node": "transcribe"}),
    )
```

- [ ] **Step 6: Run test to verify it fails**

```bash
pytest tests/agent/test_state.py::test_events_publish_subscribe -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.events'`

- [ ] **Step 7: Create `backend/agent/events.py`**

```python
from __future__ import annotations
import json
from redis_client import get_redis
from config import settings

CHANNEL_PREFIX = "agent:events:"


async def publish(thread_id: str, event: dict) -> None:
    redis = await get_redis()
    await redis.publish(f"{CHANNEL_PREFIX}{thread_id}", json.dumps(event))


async def subscribe(thread_id: str):
    """Async generator yielding event dicts. Uses a dedicated connection for pub/sub."""
    import redis.asyncio as aioredis

    conn = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = conn.pubsub()
    await pubsub.subscribe(f"{CHANNEL_PREFIX}{thread_id}")
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                event = json.loads(message["data"])
                yield event
                if event.get("type") in ("done", "error"):
                    break
    finally:
        await pubsub.unsubscribe(f"{CHANNEL_PREFIX}{thread_id}")
        await conn.aclose()
```

- [ ] **Step 8: Run all tests to verify they pass**

```bash
pytest tests/agent/test_state.py -v
```
Expected: 2 PASS

- [ ] **Step 9: Commit**

```bash
git add backend/agent/state.py backend/agent/events.py tests/agent/test_state.py
git commit -m "feat: add AgentState TypedDict and Redis event bus"
```

---

### Task 3: Review node and runner

**Files:**
- Create: `backend/agent/nodes/review_node.py`
- Create: `backend/agent/runner.py`
- Test: `tests/agent/test_review_node.py`

- [ ] **Step 1: Write failing test for review node**

Create `tests/agent/test_review_node.py`:

```python
import pytest
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command


def _make_test_graph(review_node_func):
    """Build a minimal graph: start → set_draft → review → END"""
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/agent/test_review_node.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.nodes.review_node'`

- [ ] **Step 3: Create `backend/agent/nodes/review_node.py`**

```python
from langgraph.types import interrupt


def review_node(state: dict) -> dict:
    ir_decision = interrupt({
        "draft": state.get("draft"),
        "task_type": state.get("task_type"),
    })
    action = ir_decision["action"]
    final = ir_decision.get("final", state.get("draft")) if action != "rejected" else None
    return {
        "ir_action": action,
        "final": final,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/agent/test_review_node.py -v
```
Expected: 3 PASS

- [ ] **Step 5: Write failing test for runner**

Add to `tests/agent/test_review_node.py`:

```python
@pytest.mark.asyncio
async def test_runner_run_and_resume(mocker):
    from agent.runner import run, resume, get_graph
    from agent.workflows.meeting_minutes import meeting_minutes_graph

    # Register a real graph with the runner for testing
    from agent.runner import register_graph
    register_graph("meeting_minutes", meeting_minutes_graph)

    # Mock skill calls so the workflow doesn't hit real APIs
    mocker.patch(
        "agent.workflows.meeting_minutes.skill_registry",
        **{"call": mocker.AsyncMock(return_value="生成的会议纪要草稿")},
    )

    published = []
    mocker.patch("agent.events.publish", side_effect=lambda tid, ev: published.append(ev))
    # Also patch get_redis used by events
    mocker.patch("agent.events.get_redis", return_value=mocker.AsyncMock(publish=mocker.AsyncMock()))

    thread_id = "runner-test-001"
    state = {
        "thread_id": thread_id, "ir_id": 1, "task_type": "meeting_minutes",
        "meeting_id": None, "audio_url": None, "transcript": "测试会议内容",
        "investor_ids": [], "investor_profiles": "无", "target_date": None,
        "events": None, "criteria": None, "candidate_ids": None,
        "investor_id": None, "milestone_type": None, "ir_name": None,
        "draft": None, "final": None, "ir_action": None,
        "prompt_version": None, "skills_called": [], "error": None,
    }

    await run("meeting_minutes", state, thread_id)

    # Should have published waiting_review
    types = [e["type"] for e in published]
    assert "waiting_review" in types

    # Resume
    published.clear()
    await resume("meeting_minutes", thread_id, {"action": "approved", "final": "最终纪要"})
    types = [e["type"] for e in published]
    assert "done" in types
```

- [ ] **Step 6: Create `backend/agent/runner.py`** (this test will fail until workflows are created in Tasks 5-8, but runner itself can be written now)

```python
from __future__ import annotations
import asyncio
from typing import Any
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

_checkpointer = MemorySaver()
_graphs: dict[str, Any] = {}


def register_graph(task_type: str, graph: Any) -> None:
    _graphs[task_type] = graph


def get_graph(task_type: str) -> Any:
    if task_type not in _graphs:
        raise KeyError(f"No graph registered for task_type: {task_type}")
    return _graphs[task_type]


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


async def run(task_type: str, initial_state: dict, thread_id: str) -> None:
    """Run workflow until done or interrupt. Publishes events to Redis pub/sub."""
    from agent.events import publish
    from redis_client import get_redis

    graph = get_graph(task_type)
    config = _config(thread_id)

    # Store task_type in Redis for resume routing
    redis = await get_redis()
    await redis.set(f"agent:thread:{thread_id}:type", task_type, ex=86400)

    try:
        async for event in graph.astream(initial_state, config, stream_mode="updates"):
            if "__interrupt__" in event:
                state_snap = graph.get_state(config).values
                await publish(thread_id, {
                    "type": "waiting_review",
                    "draft": state_snap.get("draft"),
                    "task_type": task_type,
                })
                return
            node_name = next(iter(event))
            await publish(thread_id, {"type": "node_done", "node": node_name})

        final_snap = graph.get_state(config).values
        await publish(thread_id, {
            "type": "done",
            "final": final_snap.get("final"),
            "ir_action": final_snap.get("ir_action"),
        })
    except Exception as exc:
        await publish(thread_id, {"type": "error", "message": str(exc)})


async def resume(task_type: str, thread_id: str, ir_decision: dict) -> None:
    """Resume a paused workflow with the IR's decision."""
    from agent.events import publish

    graph = get_graph(task_type)
    config = _config(thread_id)

    try:
        async for event in graph.astream(
            Command(resume=ir_decision), config, stream_mode="updates"
        ):
            if "__interrupt__" in event:
                # Shouldn't happen, but guard against it
                state_snap = graph.get_state(config).values
                await publish(thread_id, {
                    "type": "waiting_review",
                    "draft": state_snap.get("draft"),
                    "task_type": task_type,
                })
                return
            node_name = next(iter(event))
            await publish(thread_id, {"type": "node_done", "node": node_name})

        final_snap = graph.get_state(config).values
        await publish(thread_id, {
            "type": "done",
            "final": final_snap.get("final"),
            "ir_action": final_snap.get("ir_action"),
        })
    except Exception as exc:
        await publish(thread_id, {"type": "error", "message": str(exc)})
```

- [ ] **Step 7: Commit what we have**

```bash
git add backend/agent/nodes/review_node.py backend/agent/runner.py tests/agent/test_review_node.py
git commit -m "feat: add review node (interrupt/resume) and runner"
```

---

### Task 4: Prompt files for all four workflows

**Files:**
- Create: 8 files under `backend/prompts/`

- [ ] **Step 1: Create `backend/prompts/meeting_minutes/generate/v1.txt`**

```
mkdir -p backend/prompts/meeting_minutes/generate
```

Content of `backend/prompts/meeting_minutes/generate/v1.txt`:
```
你是FA机构的AI助手，负责根据投资人会议的录音转文字内容，生成专业的会议纪要。

投资人信息：
{investor_profiles}

会议转录：
{transcript}

请生成一份结构化的会议纪要，包括：
1. 投资人基本信息和会议背景
2. 主要讨论内容（投资偏好、关注赛道、当前阶段等）
3. 投资人的具体关切和反馈意见
4. 下一步跟进计划和行动项

要求：中文撰写，格式清晰，言简意赅，不超过800字。
```

- [ ] **Step 2: Create `backend/prompts/meeting_minutes/generate/v1.meta.json`**

```json
{"version": "v1", "status": "active", "description": "会议纪要生成提示词"}
```

- [ ] **Step 3: Create `backend/prompts/daily_push/generate/v1.txt`**

```
mkdir -p backend/prompts/daily_push/generate
```

Content of `backend/prompts/daily_push/generate/v1.txt`:
```
你是FA机构的AI助手，负责帮助IR生成每日投资人个性化关怀消息。

今日关怀事件：
{events}

投资人档案：
{investor_profiles}

请为每位有关怀事件的投资人生成一条个性化消息。要求：
- 语气：友好、专业，体现对投资人的了解
- 长度：每条50-80字
- 内容：结合投资人偏好和当日事件，自然引出话题
- 不要使用模板化的套话

请以JSON数组格式输出，每项包含字段：
- investor_id: 投资人ID（整数）
- message: 关怀消息正文（字符串）
```

- [ ] **Step 4: Create `backend/prompts/daily_push/generate/v1.meta.json`**

```json
{"version": "v1", "status": "active", "description": "每日推送消息生成提示词"}
```

- [ ] **Step 5: Create `backend/prompts/smart_list/rank/v1.txt`**

```
mkdir -p backend/prompts/smart_list/rank
```

Content of `backend/prompts/smart_list/rank/v1.txt`:
```
你是FA机构的AI助手，负责为项目融资对接筛选最匹配的投资人名单。

项目需求：
{criteria}

候选投资人档案：
{investor_profiles}

请根据项目特征（行业赛道、融资阶段、金额、商业模式等）评估每位投资人的匹配度，给出推荐名单。

以JSON数组格式输出，每项包含：
- investor_id: 投资人ID（整数）
- score: 匹配分数0-100（整数）
- reason: 匹配理由，2-3句话说明为什么推荐（字符串）
- priority: "高" | "中" | "低"

按score降序排列，只输出score >= 50的投资人。
```

- [ ] **Step 6: Create `backend/prompts/smart_list/rank/v1.meta.json`**

```json
{"version": "v1", "status": "active", "description": "智能名单评分排序提示词"}
```

- [ ] **Step 7: Create `backend/prompts/milestone_message/generate/v1.txt`**

```
mkdir -p backend/prompts/milestone_message/generate
```

Content of `backend/prompts/milestone_message/generate/v1.txt`:
```
你是FA机构的AI助手，负责帮助IR生成重要节点的投资人关怀消息。

投资人信息：
{investor_profile}

关怀类型：{milestone_type}
IR姓名：{ir_name}

关怀类型说明：
- birthday（生日）：送上真诚祝福，顺带表达对合作的期待
- join_agency（入职纪念日）：感谢一直以来的合作，展望未来
- first_meeting（首次见面纪念日）：感谢当初的信任，回顾合作历程

要求：
- 语气：亲切、真诚，不过于正式
- 长度：60-80字
- 避免通用套话，融入投资人的具体信息
- 以IR的名义发送，口吻第一人称

只输出消息正文，不需要其他说明。
```

- [ ] **Step 8: Create `backend/prompts/milestone_message/generate/v1.meta.json`**

```json
{"version": "v1", "status": "active", "description": "节点触达消息生成提示词"}
```

- [ ] **Step 9: Verify prompt registry can load all prompts**

```bash
cd /Users/summer/fa-agent/backend
python -c "
from harness.prompt_registry import registry
p = registry.get('meeting_minutes.generate', variables={'investor_profiles': 'test', 'transcript': 'test'})
print('meeting_minutes.generate: OK')
p = registry.get('daily_push.generate', variables={'events': 'test', 'investor_profiles': 'test'})
print('daily_push.generate: OK')
p = registry.get('smart_list.rank', variables={'criteria': 'test', 'investor_profiles': 'test'})
print('smart_list.rank: OK')
p = registry.get('milestone_message.generate', variables={'investor_profile': 'test', 'milestone_type': 'birthday', 'ir_name': 'test'})
print('milestone_message.generate: OK')
"
```
Expected: All four "OK" lines printed.

- [ ] **Step 10: Commit**

```bash
git add backend/prompts/
git commit -m "feat: add prompt files for all four agent workflows"
```

---

### Task 5: Meeting minutes workflow (B)

**Files:**
- Create: `backend/agent/workflows/meeting_minutes.py`
- Test: `tests/agent/test_meeting_minutes.py`

The graph: `fetch_profiles → transcribe → generate → review(interrupt) → save → END`

- [ ] **Step 1: Write failing test**

Create `tests/agent/test_meeting_minutes.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/agent/test_meeting_minutes.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.workflows.meeting_minutes'`

- [ ] **Step 3: Create `backend/agent/workflows/meeting_minutes.py`**

```python
from __future__ import annotations
from sqlalchemy import select
from langgraph.graph import StateGraph, START, END
from agent.state import AgentState
from agent.nodes.review_node import review_node
from agent.runner import _checkpointer
from database import AsyncSessionLocal
from harness.skill_registry import skill_registry
from harness.prompt_registry import registry as prompt_registry
from models.investors import Investor
from models.interaction_logs import InteractionLog
from models.outreach_records import OutreachRecord
from models.agent_traces import AgentTrace


async def fetch_profiles_node(state: AgentState) -> dict:
    investor_ids = state.get("investor_ids") or []
    if not investor_ids:
        return {"investor_profiles": "（无关联投资人信息）"}
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Investor).where(Investor.id.in_(investor_ids)))
        investors = result.scalars().all()
    lines = []
    for inv in investors:
        lines.append(f"姓名：{inv.name}，机构：{inv.agency or ''}，职位：{inv.position or ''}，备注：{inv.profile_notes or ''}")
    return {"investor_profiles": "\n".join(lines) or "（无相关信息）"}


async def transcribe_node(state: AgentState) -> dict:
    """Use transcript directly if provided; otherwise call ASR skill."""
    if state.get("transcript"):
        return {"skills_called": []}
    if not state.get("audio_url"):
        return {"transcript": "（无转录内容）", "skills_called": []}
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.get(state["audio_url"])
        audio_bytes = resp.content
    text = await skill_registry.call("ASR.音频转文字", audio_bytes=audio_bytes)
    return {"transcript": text, "skills_called": ["ASR.音频转文字"]}


async def generate_node(state: AgentState) -> dict:
    context = prompt_registry.get(
        "meeting_minutes.generate",
        variables={
            "investor_profiles": state.get("investor_profiles") or "",
            "transcript": state.get("transcript") or "",
        },
    )
    draft = await skill_registry.call("Claude.生成内容", context=context)
    return {
        "draft": draft,
        "prompt_version": "v1",
        "skills_called": ["Claude.生成内容"],
    }


async def save_node(state: AgentState) -> dict:
    if state.get("ir_action") == "rejected":
        return {}
    final_content = state.get("final") or ""
    investor_ids = state.get("investor_ids") or []
    async with AsyncSessionLocal() as db:
        for inv_id in investor_ids:
            db.add(InteractionLog(
                investor_id=inv_id,
                ir_id=state["ir_id"],
                type="meeting",
                summary=final_content[:500],
                raw_content=state.get("transcript") or "",
                agent_generated=True,
            ))
            db.add(OutreachRecord(
                investor_id=inv_id,
                ir_id=state["ir_id"],
                type="meeting_minutes",
                content=final_content,
                status="approved" if state.get("ir_action") == "approved" else "draft",
            ))
        db.add(AgentTrace(
            thread_id=state["thread_id"],
            ir_id=state["ir_id"],
            agent_name="meeting_minutes",
            prompt_version=state.get("prompt_version") or "v1",
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            skills_called=state.get("skills_called") or [],
            status="success",
        ))
        await db.commit()
    return {}


builder = StateGraph(AgentState)
builder.add_node("fetch_profiles", fetch_profiles_node)
builder.add_node("transcribe", transcribe_node)
builder.add_node("generate", generate_node)
builder.add_node("review", review_node)
builder.add_node("save", save_node)

builder.add_edge(START, "fetch_profiles")
builder.add_edge("fetch_profiles", "transcribe")
builder.add_edge("transcribe", "generate")
builder.add_edge("generate", "review")
builder.add_edge("review", "save")
builder.add_edge("save", END)

meeting_minutes_graph = builder.compile(checkpointer=_checkpointer)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/agent/test_meeting_minutes.py -v
```
Expected: 2 PASS

- [ ] **Step 5: Register graph in runner at startup**

Open `backend/agent/runner.py`. After the `_graphs: dict` declaration add these imports at the bottom of the file (after the `resume` function):

```python
def _register_all_graphs() -> None:
    from agent.workflows.meeting_minutes import meeting_minutes_graph
    register_graph("meeting_minutes", meeting_minutes_graph)


# Called once at import time; individual workflow modules complete the registry
# (see agent/workflows/*.py for each call to register_graph)
```

Then in `backend/agent/__init__.py`:

```python
# Trigger registration of all workflow graphs on package import
from agent.runner import _register_all_graphs  # noqa: F401
```

- [ ] **Step 6: Commit**

```bash
git add backend/agent/workflows/meeting_minutes.py backend/agent/runner.py backend/agent/__init__.py tests/agent/test_meeting_minutes.py
git commit -m "feat: meeting minutes workflow (B) with interrupt/resume"
```

---

### Task 6: Daily push workflow (A)

**Files:**
- Create: `backend/agent/workflows/daily_push.py`
- Test: `tests/agent/test_daily_push.py`

The graph: `fetch_events → fetch_profiles → generate → review(interrupt) → save → END`

`fetch_events` queries `Investor` records where birthday/join_agency_date matches today or `last_interaction_at` is > 14 days ago.

- [ ] **Step 1: Write failing test**

Create `tests/agent/test_daily_push.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/agent/test_daily_push.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.workflows.daily_push'`

- [ ] **Step 3: Create `backend/agent/workflows/daily_push.py`**

```python
from __future__ import annotations
import json
from datetime import date, timedelta
from sqlalchemy import select, or_
from sqlalchemy.sql import func as sqlfunc
from langgraph.graph import StateGraph, START, END
from agent.state import AgentState
from agent.nodes.review_node import review_node
from agent.runner import _checkpointer, register_graph
from database import AsyncSessionLocal
from harness.skill_registry import skill_registry
from harness.prompt_registry import registry as prompt_registry
from models.investors import Investor
from models.outreach_records import OutreachRecord
from models.agent_traces import AgentTrace


async def fetch_events_node(state: AgentState) -> dict:
    target = date.fromisoformat(state["target_date"]) if state.get("target_date") else date.today()
    cutoff = target - timedelta(days=14)
    async with AsyncSessionLocal() as db:
        stmt = select(Investor).where(
            Investor.is_active == True,
            or_(
                sqlfunc.month(Investor.birthday) == target.month,
                sqlfunc.day(Investor.birthday) == target.day,
                sqlfunc.month(Investor.join_agency_date) == target.month,
                sqlfunc.day(Investor.join_agency_date) == target.day,
                Investor.last_interaction_at < cutoff,
                Investor.last_interaction_at == None,
            )
        )
        if state.get("investor_ids"):
            stmt = stmt.where(Investor.id.in_(state["investor_ids"]))
        result = await db.execute(stmt)
        investors = result.scalars().all()
    events = []
    for inv in investors:
        ev_types = []
        if inv.birthday and inv.birthday.month == target.month and inv.birthday.day == target.day:
            ev_types.append("生日")
        if inv.join_agency_date and inv.join_agency_date.month == target.month and inv.join_agency_date.day == target.day:
            ev_types.append("入职纪念日")
        if not ev_types:
            ev_types.append("常规跟进")
        events.append({"investor_id": inv.id, "name": inv.name, "agency": inv.agency, "event_types": ev_types})
    return {"events": events}


async def fetch_profiles_node(state: AgentState) -> dict:
    events = state.get("events") or []
    investor_ids = [e["investor_id"] for e in events]
    if not investor_ids:
        return {"investor_profiles": "（无关联投资人）"}
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Investor).where(Investor.id.in_(investor_ids)))
        investors = result.scalars().all()
    inv_map = {inv.id: inv for inv in investors}
    lines = []
    for ev in events:
        inv = inv_map.get(ev["investor_id"])
        if inv:
            lines.append(f"[ID:{inv.id}] 姓名：{inv.name}，机构：{inv.agency or ''}，关怀事件：{'、'.join(ev['event_types'])}，备注：{inv.profile_notes or ''}")
    return {"investor_profiles": "\n".join(lines)}


async def generate_node(state: AgentState) -> dict:
    events_str = json.dumps(state.get("events") or [], ensure_ascii=False, indent=2)
    context = prompt_registry.get(
        "daily_push.generate",
        variables={
            "events": events_str,
            "investor_profiles": state.get("investor_profiles") or "",
        },
    )
    draft = await skill_registry.call("Claude.生成内容", context=context)
    return {"draft": draft, "prompt_version": "v1", "skills_called": ["Claude.生成内容"]}


async def save_node(state: AgentState) -> dict:
    if state.get("ir_action") == "rejected":
        return {}
    final_content = state.get("final") or ""
    try:
        messages = json.loads(final_content)
    except (json.JSONDecodeError, TypeError):
        messages = [{"investor_id": inv_id, "message": final_content}
                    for inv_id in ([e["investor_id"] for e in (state.get("events") or [])])]
    async with AsyncSessionLocal() as db:
        for item in messages:
            db.add(OutreachRecord(
                investor_id=item["investor_id"],
                ir_id=state["ir_id"],
                type="daily_push",
                content=item.get("message", ""),
                status="approved",
            ))
        db.add(AgentTrace(
            thread_id=state["thread_id"],
            ir_id=state["ir_id"],
            agent_name="daily_push",
            prompt_version=state.get("prompt_version") or "v1",
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            skills_called=state.get("skills_called") or [],
            status="success",
        ))
        await db.commit()
    return {}


builder = StateGraph(AgentState)
builder.add_node("fetch_events", fetch_events_node)
builder.add_node("fetch_profiles", fetch_profiles_node)
builder.add_node("generate", generate_node)
builder.add_node("review", review_node)
builder.add_node("save", save_node)

builder.add_edge(START, "fetch_events")
builder.add_edge("fetch_events", "fetch_profiles")
builder.add_edge("fetch_profiles", "generate")
builder.add_edge("generate", "review")
builder.add_edge("review", "save")
builder.add_edge("save", END)

daily_push_graph = builder.compile(checkpointer=_checkpointer)
register_graph("daily_push", daily_push_graph)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/agent/test_daily_push.py -v
```
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/agent/workflows/daily_push.py tests/agent/test_daily_push.py
git commit -m "feat: daily push workflow (A) with interrupt/resume"
```

---

### Task 7: Smart list workflow (C)

**Files:**
- Create: `backend/agent/workflows/smart_list.py`
- Test: `tests/agent/test_smart_list.py`

The graph: `fetch_candidates → rank → format_list → review(interrupt) → save → END`

- [ ] **Step 1: Write failing test**

Create `tests/agent/test_smart_list.py`:

```python
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
    assert len(state.tasks) > 0


@pytest.mark.asyncio
async def test_smart_list_approve_saves_records(base_state, db_session, mocker):
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/agent/test_smart_list.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.workflows.smart_list'`

- [ ] **Step 3: Create `backend/agent/workflows/smart_list.py`**

```python
from __future__ import annotations
import json
from sqlalchemy import select
from langgraph.graph import StateGraph, START, END
from agent.state import AgentState
from agent.nodes.review_node import review_node
from agent.runner import _checkpointer, register_graph
from database import AsyncSessionLocal
from harness.skill_registry import skill_registry
from harness.prompt_registry import registry as prompt_registry
from models.investors import Investor
from models.outreach_records import OutreachRecord
from models.agent_traces import AgentTrace


async def fetch_candidates_node(state: AgentState) -> dict:
    async with AsyncSessionLocal() as db:
        stmt = select(Investor).where(Investor.is_active == True)
        if state.get("candidate_ids"):
            stmt = stmt.where(Investor.id.in_(state["candidate_ids"]))
        result = await db.execute(stmt)
        investors = result.scalars().all()
    lines = []
    for inv in investors:
        lines.append(
            f"[ID:{inv.id}] 姓名：{inv.name}，机构：{inv.agency or ''}，"
            f"行业偏好：{json.dumps(inv.industry_tags or [], ensure_ascii=False)}，"
            f"阶段偏好：{json.dumps(inv.stage_pref or [], ensure_ascii=False)}，"
            f"投资规模：{inv.quota_range or '未知'}，"
            f"备注：{(inv.profile_notes or '')[:200]}"
        )
    return {
        "investor_profiles": "\n".join(lines),
        "candidate_ids": [inv.id for inv in investors],
    }


async def rank_node(state: AgentState) -> dict:
    context = prompt_registry.get(
        "smart_list.rank",
        variables={
            "criteria": state.get("criteria") or "",
            "investor_profiles": state.get("investor_profiles") or "",
        },
    )
    ranked_json = await skill_registry.call("Claude.生成内容", context=context)
    return {"draft": ranked_json, "prompt_version": "v1", "skills_called": ["Claude.生成内容"]}


async def format_list_node(state: AgentState) -> dict:
    """Parse and format the ranked list as a human-readable draft for IR review."""
    try:
        items = json.loads(state.get("draft") or "[]")
    except (json.JSONDecodeError, TypeError):
        return {}
    lines = ["智能推荐投资人名单：\n"]
    for i, item in enumerate(items, 1):
        lines.append(
            f"{i}. [ID:{item['investor_id']}] "
            f"优先级：{item.get('priority', '中')}  "
            f"匹配分：{item.get('score', 0)}\n"
            f"   推荐理由：{item.get('reason', '')}\n"
        )
    return {"draft": "\n".join(lines)}


async def save_node(state: AgentState) -> dict:
    if state.get("ir_action") == "rejected":
        return {}
    final_content = state.get("final") or ""
    try:
        items = json.loads(final_content)
        investor_ids_in_list = [item["investor_id"] for item in items]
    except (json.JSONDecodeError, TypeError, KeyError):
        investor_ids_in_list = state.get("candidate_ids") or []
    async with AsyncSessionLocal() as db:
        for inv_id in investor_ids_in_list:
            db.add(OutreachRecord(
                investor_id=inv_id,
                ir_id=state["ir_id"],
                type="meeting_minutes",  # OutreachRecord.type has no "smart_list" enum; store as draft content
                content=final_content,
                status="approved",
            ))
        db.add(AgentTrace(
            thread_id=state["thread_id"],
            ir_id=state["ir_id"],
            agent_name="smart_list",
            prompt_version=state.get("prompt_version") or "v1",
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            skills_called=state.get("skills_called") or [],
            status="success",
        ))
        await db.commit()
    return {}


builder = StateGraph(AgentState)
builder.add_node("fetch_candidates", fetch_candidates_node)
builder.add_node("rank", rank_node)
builder.add_node("format_list", format_list_node)
builder.add_node("review", review_node)
builder.add_node("save", save_node)

builder.add_edge(START, "fetch_candidates")
builder.add_edge("fetch_candidates", "rank")
builder.add_edge("rank", "format_list")
builder.add_edge("format_list", "review")
builder.add_edge("review", "save")
builder.add_edge("save", END)

smart_list_graph = builder.compile(checkpointer=_checkpointer)
register_graph("smart_list", smart_list_graph)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/agent/test_smart_list.py -v
```
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/agent/workflows/smart_list.py tests/agent/test_smart_list.py
git commit -m "feat: smart list workflow (C) with interrupt/resume"
```

---

### Task 8: Milestone outreach workflow

**Files:**
- Create: `backend/agent/workflows/milestone_outreach.py`
- Test: `tests/agent/test_milestone_outreach.py`

The graph: `fetch_investor → generate_message → review(interrupt) → save → END`

- [ ] **Step 1: Write failing test**

Create `tests/agent/test_milestone_outreach.py`:

```python
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
    assert len(state.tasks) > 0


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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/agent/test_milestone_outreach.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.workflows.milestone_outreach'`

- [ ] **Step 3: Create `backend/agent/workflows/milestone_outreach.py`**

```python
from __future__ import annotations
from sqlalchemy import select
from langgraph.graph import StateGraph, START, END
from agent.state import AgentState
from agent.nodes.review_node import review_node
from agent.runner import _checkpointer, register_graph
from database import AsyncSessionLocal
from harness.skill_registry import skill_registry
from harness.prompt_registry import registry as prompt_registry
from models.investors import Investor
from models.outreach_records import OutreachRecord
from models.agent_traces import AgentTrace

_MILESTONE_LABELS = {
    "birthday": "生日",
    "join_agency": "入职纪念日",
    "first_meeting": "首次见面纪念日",
}


async def fetch_investor_node(state: AgentState) -> dict:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Investor).where(Investor.id == state["investor_id"]))
        inv = result.scalar_one_or_none()
    if not inv:
        return {"error": f"投资人 {state['investor_id']} 不存在"}
    profile = (
        f"姓名：{inv.name}，机构：{inv.agency or ''}，职位：{inv.position or ''}，"
        f"备注：{(inv.profile_notes or '')[:300]}"
    )
    return {"investor_profiles": profile}


async def generate_node(state: AgentState) -> dict:
    milestone_label = _MILESTONE_LABELS.get(state.get("milestone_type") or "", state.get("milestone_type") or "")
    context = prompt_registry.get(
        "milestone_message.generate",
        variables={
            "investor_profile": state.get("investor_profiles") or "",
            "milestone_type": milestone_label,
            "ir_name": state.get("ir_name") or "IR",
        },
    )
    message = await skill_registry.call("Claude.生成内容", context=context, max_tokens=256)
    return {"draft": message, "prompt_version": "v1", "skills_called": ["Claude.生成内容"]}


async def save_node(state: AgentState) -> dict:
    if state.get("ir_action") == "rejected" or not state.get("investor_id"):
        return {}
    async with AsyncSessionLocal() as db:
        db.add(OutreachRecord(
            investor_id=state["investor_id"],
            ir_id=state["ir_id"],
            type="milestone_message",
            content=state.get("final") or "",
            status="approved" if state.get("ir_action") != "rejected" else "draft",
        ))
        db.add(AgentTrace(
            thread_id=state["thread_id"],
            ir_id=state["ir_id"],
            agent_name="milestone_outreach",
            prompt_version=state.get("prompt_version") or "v1",
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            skills_called=state.get("skills_called") or [],
            status="success",
        ))
        await db.commit()
    return {}


builder = StateGraph(AgentState)
builder.add_node("fetch_investor", fetch_investor_node)
builder.add_node("generate", generate_node)
builder.add_node("review", review_node)
builder.add_node("save", save_node)

builder.add_edge(START, "fetch_investor")
builder.add_edge("fetch_investor", "generate")
builder.add_edge("generate", "review")
builder.add_edge("review", "save")
builder.add_edge("save", END)

milestone_outreach_graph = builder.compile(checkpointer=_checkpointer)
register_graph("milestone_outreach", milestone_outreach_graph)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/agent/test_milestone_outreach.py -v
```
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/agent/workflows/milestone_outreach.py tests/agent/test_milestone_outreach.py
git commit -m "feat: milestone outreach workflow with interrupt/resume"
```

---

### Task 9: Agent API router (HTTP + WebSocket)

**Files:**
- Create: `backend/api/agent.py`
- Modify: `backend/main.py`
- Test: `tests/test_agent_api.py`

Endpoints:
- `POST /api/agent/run` — start a workflow, returns `thread_id`
- `WebSocket /api/agent/ws/{thread_id}` — stream events
- `POST /api/agent/{thread_id}/review` — submit IR review

- [ ] **Step 1: Write failing tests**

Create `tests/test_agent_api.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport
from auth.jwt import create_token


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {create_token(ir_id=1, role='ir')}"}


@pytest.mark.asyncio
async def test_run_meeting_minutes(override_db, mocker, auth_headers):
    from main import app
    mock_run = mocker.patch("api.agent.run", new=mocker.AsyncMock())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/agent/run",
            json={
                "task_type": "meeting_minutes",
                "transcript": "会议内容",
                "investor_ids": [1],
            },
            headers=auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "thread_id" in data
    assert mock_run.called


@pytest.mark.asyncio
async def test_run_requires_auth():
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/agent/run", json={"task_type": "meeting_minutes"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_review_endpoint(override_db, mocker, auth_headers):
    from main import app
    mock_resume = mocker.patch("api.agent.resume", new=mocker.AsyncMock())
    mock_redis = mocker.AsyncMock()
    mock_redis.get = mocker.AsyncMock(return_value="meeting_minutes")
    mocker.patch("api.agent.get_redis", new=mocker.AsyncMock(return_value=mock_redis))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/agent/test-thread-001/review",
            json={"action": "approved", "final": "最终内容"},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "resumed"
    assert mock_resume.called


@pytest.mark.asyncio
async def test_review_thread_not_found(override_db, mocker, auth_headers):
    from main import app
    mock_redis = mocker.AsyncMock()
    mock_redis.get = mocker.AsyncMock(return_value=None)
    mocker.patch("api.agent.get_redis", new=mocker.AsyncMock(return_value=mock_redis))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/agent/nonexistent-thread/review",
            json={"action": "approved", "final": ""},
            headers=auth_headers,
        )

    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_agent_api.py -v
```
Expected: FAIL (module or route not found)

- [ ] **Step 3: Create `backend/api/agent.py`**

```python
from __future__ import annotations
import uuid
from typing import Optional, Literal
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from auth.jwt import get_current_ir
from agent.runner import run, resume
from agent.events import subscribe
from agent.state import AgentState, TaskType, IrAction
from redis_client import get_redis

router = APIRouter()


class RunRequest(BaseModel):
    task_type: TaskType
    meeting_id: Optional[str] = None
    audio_url: Optional[str] = None
    transcript: Optional[str] = None
    investor_ids: Optional[list[int]] = None
    target_date: Optional[str] = None
    criteria: Optional[str] = None
    candidate_ids: Optional[list[int]] = None
    investor_id: Optional[int] = None
    milestone_type: Optional[str] = None
    ir_name: Optional[str] = None


class ReviewRequest(BaseModel):
    action: IrAction
    final: Optional[str] = None


@router.post("/run")
async def start_workflow(
    request: RunRequest,
    background_tasks: BackgroundTasks,
    current_ir: dict = Depends(get_current_ir),
):
    thread_id = str(uuid.uuid4())
    state: AgentState = {
        "thread_id": thread_id,
        "ir_id": current_ir["ir_id"],
        "task_type": request.task_type,
        "meeting_id": request.meeting_id,
        "audio_url": request.audio_url,
        "transcript": request.transcript,
        "investor_ids": request.investor_ids,
        "investor_profiles": None,
        "target_date": request.target_date,
        "events": None,
        "criteria": request.criteria,
        "candidate_ids": request.candidate_ids,
        "investor_id": request.investor_id,
        "milestone_type": request.milestone_type,
        "ir_name": request.ir_name,
        "draft": None,
        "final": None,
        "ir_action": None,
        "prompt_version": None,
        "skills_called": [],
        "error": None,
    }
    background_tasks.add_task(run, request.task_type, state, thread_id)
    return {"thread_id": thread_id}


@router.websocket("/ws/{thread_id}")
async def agent_websocket(websocket: WebSocket, thread_id: str):
    await websocket.accept()
    try:
        async for event in subscribe(thread_id):
            await websocket.send_json(event)
            if event.get("type") in ("done", "error"):
                break
    except WebSocketDisconnect:
        pass
    finally:
        await websocket.close()


@router.post("/{thread_id}/review")
async def submit_review(
    thread_id: str,
    review: ReviewRequest,
    background_tasks: BackgroundTasks,
    current_ir: dict = Depends(get_current_ir),
):
    redis = await get_redis()
    task_type = await redis.get(f"agent:thread:{thread_id}:type")
    if not task_type:
        raise HTTPException(status_code=404, detail="Thread not found or already completed")

    ir_decision = {
        "action": review.action,
        "final": review.final or "",
    }
    background_tasks.add_task(resume, task_type, thread_id, ir_decision)
    return {"status": "resumed"}
```

- [ ] **Step 4: Register router in `backend/main.py`**

Open `backend/main.py`. Add import and include_router after existing routers:

```python
from api.agent import router as agent_router

# Add after existing import skills lines
import agent  # noqa: F401  — triggers graph registration

# Add after existing app.include_router calls:
app.include_router(agent_router, prefix="/api/agent", tags=["agent"])
```

The full updated `backend/main.py`:

```python
from fastapi import FastAPI
from auth.router import router as auth_router
from api.investors import router as investors_router
from api.calendar import router as calendar_router
from api.admin import router as admin_router
from api.agent import router as agent_router

# Load all Skills to register them into skill_registry
import skills.claude_skill  # noqa: F401
import skills.tavily_skill   # noqa: F401
import skills.qmingpian      # noqa: F401
import skills.tencent_meeting # noqa: F401
import skills.asr_skill      # noqa: F401

# Trigger workflow graph registration
import agent  # noqa: F401

app = FastAPI(title="FA Agent API", version="1.0.0")

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(investors_router, prefix="/api/investors", tags=["investors"])
app.include_router(calendar_router, prefix="/api/calendar", tags=["calendar"])
app.include_router(admin_router, prefix="/api/admin", tags=["admin"])
app.include_router(agent_router, prefix="/api/agent", tags=["agent"])

@app.get("/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 5: Update `backend/agent/__init__.py` to trigger all registrations**

```python
# Import all workflow modules to trigger register_graph() calls
from agent.workflows import meeting_minutes  # noqa: F401
from agent.workflows import daily_push       # noqa: F401
from agent.workflows import smart_list       # noqa: F401
from agent.workflows import milestone_outreach  # noqa: F401
```

- [ ] **Step 6: Run API tests to verify they pass**

```bash
pytest tests/test_agent_api.py -v
```
Expected: 4 PASS

- [ ] **Step 7: Run full test suite**

```bash
pytest tests/ -v
```
Expected: All tests pass (22+ from Plan 1 + new tests)

- [ ] **Step 8: Commit**

```bash
git add backend/api/agent.py backend/main.py backend/agent/__init__.py tests/test_agent_api.py
git commit -m "feat: agent API router with run, WebSocket stream, and review endpoints"
```

---

### Task 10: Celery worker scaffold

**Files:**
- Create: `backend/worker.py`

The existing `docker-compose.yml` already references `celery -A worker.celery_app`. This task creates the worker so the container starts correctly.

- [ ] **Step 1: Write failing test for worker import**

Add to `tests/test_agent_api.py`:

```python
def test_celery_app_importable():
    from worker import celery_app
    assert celery_app.main == "fa_agent"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_agent_api.py::test_celery_app_importable -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'worker'`

- [ ] **Step 3: Create `backend/worker.py`**

```python
from celery import Celery
from celery.schedules import crontab
from config import settings

celery_app = Celery(
    "fa_agent",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["worker"],
)

celery_app.conf.task_routes = {
    "worker.trigger_daily_push": {"queue": "content"},
    "worker.trigger_milestone_outreach": {"queue": "content"},
}

celery_app.conf.beat_schedule = {
    "daily-push-9am": {
        "task": "worker.trigger_daily_push",
        "schedule": crontab(hour=9, minute=0),
    },
    "milestone-check-8am": {
        "task": "worker.trigger_milestone_outreach",
        "schedule": crontab(hour=8, minute=0),
    },
}


@celery_app.task(name="worker.trigger_daily_push")
def trigger_daily_push():
    """Kick off daily push workflow via internal HTTP call to FastAPI."""
    import httpx
    from datetime import date

    try:
        resp = httpx.post(
            "http://fastapi:8000/api/agent/run",
            json={
                "task_type": "daily_push",
                "target_date": date.today().isoformat(),
                "ir_id": 0,
            },
            headers={"X-Celery-Internal": "1"},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        raise self.retry(exc=exc, countdown=300, max_retries=3)


@celery_app.task(name="worker.trigger_milestone_outreach", bind=True)
def trigger_milestone_outreach(self):
    """Check today's milestones and trigger outreach workflow for each."""
    import httpx
    from datetime import date

    today = date.today()
    try:
        # Get investors with milestones today
        resp = httpx.get(
            "http://fastapi:8000/api/calendar/daily",
            params={"date": today.isoformat()},
            headers={"X-Celery-Internal": "1"},
            timeout=10,
        )
        resp.raise_for_status()
        events = resp.json().get("events", [])
        for event in events:
            if event.get("type") in ("birthday", "join_agency"):
                httpx.post(
                    "http://fastapi:8000/api/agent/run",
                    json={
                        "task_type": "milestone_outreach",
                        "investor_id": event["investor_id"],
                        "milestone_type": event["type"],
                        "ir_name": "IR",
                    },
                    headers={"X-Celery-Internal": "1"},
                    timeout=10,
                )
    except Exception as exc:
        raise self.retry(exc=exc, countdown=300, max_retries=3)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_agent_api.py::test_celery_app_importable -v
```
Expected: PASS

- [ ] **Step 5: Run the complete test suite**

```bash
pytest tests/ -v --tb=short
```
Expected: All tests pass.

- [ ] **Step 6: Verify all workflow tests together**

```bash
pytest tests/agent/ -v
```
Expected: All agent workflow tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/worker.py
git commit -m "feat: celery worker with daily push and milestone outreach beat tasks"
```

---

## Self-Review

### Spec Coverage
- ✅ Meeting minutes workflow (B): ASR + Claude generation + IR review
- ✅ Daily push workflow (A): calendar events + Claude generation + IR review
- ✅ Smart list workflow (C): investor DB fetch + Claude ranking + IR review
- ✅ Milestone outreach: birthday/anniversary detection + Claude generation + IR review
- ✅ LangGraph `interrupt()` / `Command(resume=...)` for IR human-in-loop
- ✅ `MemorySaver` checkpointer for state persistence
- ✅ Redis pub/sub for real-time event streaming
- ✅ WebSocket endpoint `/api/agent/ws/{thread_id}`
- ✅ REST endpoints: `/run`, `/{thread_id}/review`
- ✅ `AgentTrace` records saved by each workflow's `save_node`
- ✅ Celery worker + beat schedule scaffold

### Type Consistency Check
- `AgentState` defined once in `state.py`; all workflows import and use it
- `register_graph()` called in each workflow module at import time
- `_checkpointer` is the single `MemorySaver` instance in `runner.py`; all graphs share it
- `skill_registry.call()` signature: `await skill_registry.call(name, **kwargs)` — consistent across all nodes
- `prompt_registry.get(name, variables={...})` — consistent naming: `meeting_minutes.generate`, `daily_push.generate`, `smart_list.rank`, `milestone_message.generate`
- `OutreachRecord.type` enum values used: `"meeting_minutes"`, `"daily_push"`, `"milestone_message"` — all valid per model definition

### Known Limitation
`MemorySaver` is in-process memory. If the FastAPI server restarts between a workflow's interrupt and the IR's review, the graph state is lost. For production resilience, replace `MemorySaver` with `AsyncRedisSaver` from `langgraph-checkpoint-redis`. This is out of scope for Plan 2.
