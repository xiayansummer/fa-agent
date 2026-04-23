from openai import AsyncOpenAI
from harness.skill_registry import skill, skill_registry
from config import settings

_client = AsyncOpenAI(
    api_key=settings.ai_api_key,
    base_url=settings.ai_base_url,
)

@skill(registry=skill_registry, name="Claude.生成内容",
       version="1.0", timeout=90, retry=1)
async def claude_generate(context: str, max_tokens: int = 2048,
                           temperature: float = 0.3) -> str:
    response = await _client.chat.completions.create(
        model=settings.ai_model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": context}],
    )
    return response.choices[0].message.content
