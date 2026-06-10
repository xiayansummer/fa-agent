import asyncio
import logging
from openai import AsyncOpenAI
from harness.skill_registry import skill, skill_registry
from config import settings

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(
    api_key=settings.ai_api_key,
    base_url=settings.ai_base_url,
)

@skill(registry=skill_registry, name="Claude.生成内容",
       version="1.0", timeout=90, retry=1)
async def claude_generate(context: str, max_tokens: int = 2048,
                           temperature: float = 0.3) -> str:
    # minimax 等模型偶发返回空内容（finish=stop 但 content 为空）。
    # 空响应不是异常、不会触发 skill 层 retry，所以这里内部重试几次；
    # 连续空才抛异常，让上层走错误卡而不是落一张空白草稿。
    last = ""
    for attempt in range(3):
        response = await _client.chat.completions.create(
            model=settings.ai_model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": context}],
        )
        last = (response.choices[0].message.content or "").strip()
        if last:
            return last
        logger.warning("claude_generate 第 %d 次返回空内容，重试中", attempt + 1)
        await asyncio.sleep(0.6 * (attempt + 1))
    raise RuntimeError("LLM 连续返回空内容（已重试 3 次）")
