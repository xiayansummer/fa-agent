from __future__ import annotations

def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中文按字符，英文按字符，约 1.3 倍系数"""
    chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
    english_chars = sum(1 for c in text if ord(c) < 128 and c.isalpha())
    return int((chinese_chars + english_chars) * 1.3) + 1

class ContextBuilder:
    def __init__(self, token_budget: int = 4096):
        self.budget = token_budget
        self.segments: list[tuple[int, str, str]] = []  # (priority, content, label)

    def add_system_prompt(self, content: str) -> ContextBuilder:
        self.segments.append((0, content, "system"))
        return self

    def add_investor_profile(self, profile_notes: str) -> ContextBuilder:
        self.segments.append((1, profile_notes, "investor_profile"))
        return self

    def add_recent_interactions(self, interactions: str) -> ContextBuilder:
        self.segments.append((2, interactions, "recent_interactions"))
        return self

    def add_ir_context(self, context: str) -> ContextBuilder:
        self.segments.append((3, context, "ir_context"))
        return self

    def add_task_input(self, content: str) -> ContextBuilder:
        self.segments.append((4, content, "task_input"))
        return self

    def build(self) -> str:
        sorted_segments = sorted(self.segments, key=lambda x: x[0])
        result: list[str] = []
        used_tokens = 0

        for _, content, _ in sorted_segments:
            tokens = estimate_tokens(content)
            if used_tokens + tokens <= self.budget:
                result.append(content)
                used_tokens += tokens
            # No break — continue checking smaller segments after skipping a large one

        return "\n\n".join(result)
