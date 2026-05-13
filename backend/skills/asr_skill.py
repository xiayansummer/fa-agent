"""ASR 技能：DashScope Qwen3-ASR-Flash（原生 multimodal-generation 接口）。

之前用腾讯云单句识别（1 分钟上限）；现在切到 DashScope qwen3-asr-flash，
支持长音频 + 情绪 + 语种识别副产物。

API 形态：DashScope 不在 OpenAI compatible 模式暴露 ASR，必须用 native
endpoint `/api/v1/services/aigc/multimodal-generation/generation`，
消息体 `content` 里给一个 {"audio": "<url>"}，由 DashScope 服务端拉取
音频做转写。所以本 skill 接收 audio_url（要求公网可访问，例如 Qiniu 签名 URL）。
"""
import httpx
from harness.skill_registry import skill, skill_registry
from config import settings

DASHSCOPE_MULTIMODAL_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"


@skill(registry=skill_registry, name="ASR.音频转文字",
       version="2.0", timeout=180, retry=1)
async def asr_transcribe(audio_url: str) -> str:
    """把音频转写为文字。

    audio_url 必须是公网可访问的 URL（Qiniu 签名 URL 即可），DashScope 服务端
    会自己拉取。
    """
    api_key = settings.asr_api_key or settings.ai_api_key
    body = {
        "model": settings.asr_model,
        "input": {
            "messages": [
                {"role": "user", "content": [{"audio": audio_url}]},
            ],
        },
        "parameters": {},
    }
    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(
            DASHSCOPE_MULTIMODAL_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"DashScope ASR HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    try:
        choices = data["output"]["choices"]
        content = choices[0]["message"]["content"]
        # content 是 list，找第一个有 text 的项
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                return item["text"]
        return ""
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"DashScope ASR 返回结构异常: {e}, raw={str(data)[:300]}")
