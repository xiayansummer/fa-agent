"""Orchestrator 工具集 —— 按 agent 角色分组。

每个 module 声明：
- TOOLS: list[dict]      # OpenAI function-call schema
- AGENT_ROLE: str         # 该 module 工具产物归属的 agent 颜色 / 标签
- async dispatch(name, args, ctx) -> dict

chat 端点统一注册 + 路由，新加工具只需在对应 agent module 增加即可，
不需要再改 Orchestrator 的 dispatch 表。
"""
from . import direct, content, outreach, list_agent

_MODULES = [direct, content, outreach, list_agent]

ALL_TOOLS: list[dict] = [t for m in _MODULES for t in m.TOOLS]
TOOL_OWNER: dict[str, object] = {
    t["function"]["name"]: m
    for m in _MODULES
    for t in m.TOOLS
}
