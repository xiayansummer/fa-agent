import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
from harness.prompt_registry import PromptRegistry

@pytest.fixture
def registry(tmp_path):
    prompt_dir = tmp_path / "orchestrator" / "intent_routing"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "v1.txt").write_text("你是一个 {role}，请处理：{task}")
    (prompt_dir / "v1.meta.json").write_text(
        '{"version":"v1","status":"active","model":"claude-sonnet-4-6","max_tokens":1024,"temperature":0.3}'
    )
    return PromptRegistry(base_dir=str(tmp_path))

def test_get_prompt_with_variables(registry):
    result = registry.get("orchestrator.intent_routing", variables={"role": "助手", "task": "分类"})
    assert result == "你是一个 助手，请处理：分类"

def test_get_prompt_active_version(registry):
    result = registry.get("orchestrator.intent_routing")
    assert "{role}" in result

def test_get_prompt_meta(registry):
    meta = registry.get_meta("orchestrator.intent_routing")
    assert meta["model"] == "claude-sonnet-4-6"
    assert meta["status"] == "active"

def test_get_nonexistent_prompt_raises(registry):
    with pytest.raises(FileNotFoundError):
        registry.get("nonexistent.prompt")
