import anthropic
from harness.skill_registry import skill, skill_registry
from config import settings

_client = anthropic.AsyncAnthropic(api_key=settings.claude_api_key)

@skill(registry=skill_registry, name="Claude.生成内容",
       version="1.0", timeout=90, retry=1)
async def claude_generate(context: str, max_tokens: int = 2048,
                           temperature: float = 0.3) -> str:
    message = await _client.messages.create(
        model=settings.claude_model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": context}],
    )
    return message.content[0].text
