import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
from harness.skill_registry import SkillRegistry, skill

registry = SkillRegistry()

@skill(registry=registry, name="test.echo", version="1.0", timeout=5, retry=1)
async def echo_skill(text: str) -> str:
    return f"echo:{text}"

@skill(registry=registry, name="test.always_fail", version="1.0", timeout=5, retry=2,
       fallback="fallback_result")
async def always_fail_skill(text: str) -> str:
    raise RuntimeError("always fails")

@pytest.mark.asyncio
async def test_skill_success():
    result = await registry.call("test.echo", text="hello")
    assert result == "echo:hello"

@pytest.mark.asyncio
async def test_skill_fallback_on_failure():
    result = await registry.call("test.always_fail", text="x")
    assert result == "fallback_result"

@pytest.mark.asyncio
async def test_skill_raises_when_no_fallback():
    @skill(registry=registry, name="test.no_fallback", version="1.0", timeout=5, retry=1)
    async def no_fallback():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await registry.call("test.no_fallback")

def test_skill_registered():
    assert "test.echo" in registry._skills
