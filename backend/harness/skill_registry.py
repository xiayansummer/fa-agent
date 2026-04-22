from __future__ import annotations
import asyncio
from typing import Any, Callable

class SkillRegistry:
    def __init__(self):
        self._skills: dict[str, dict] = {}

    def register(self, name: str, version: str, func: Callable,
                 timeout: int, retry: int, fallback: Any = None):
        self._skills[name] = {
            "func": func,
            "version": version,
            "timeout": timeout,
            "retry": retry,
            "fallback": fallback,
        }

    async def call(self, name: str, **kwargs) -> Any:
        if name not in self._skills:
            raise KeyError(f"Skill not registered: {name}")

        s = self._skills[name]
        last_exc = None

        for attempt in range(s["retry"]):
            try:
                return await asyncio.wait_for(
                    s["func"](**kwargs),
                    timeout=s["timeout"]
                )
            except Exception as e:
                last_exc = e
                if attempt < s["retry"] - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))

        if s["fallback"] is not None:
            return s["fallback"]
        raise last_exc


def skill(registry: SkillRegistry, name: str, version: str,
          timeout: int, retry: int, fallback: Any = None):
    def decorator(func: Callable) -> Callable:
        registry.register(name, version, func, timeout, retry, fallback)
        return func
    return decorator


# 全局注册表
skill_registry = SkillRegistry()
