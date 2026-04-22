import base64
import httpx
from harness.skill_registry import skill, skill_registry
from config import settings

@skill(registry=skill_registry, name="ASR.音频转文字",
       version="1.0", timeout=120, retry=1)
async def asr_transcribe(audio_bytes: bytes, audio_format: str = "mp3") -> str:
    encoded = base64.b64encode(audio_bytes).decode()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://asr.tencentcloudapi.com/",
            headers={"Content-Type": "application/json",
                     "X-TC-Action": "SentenceRecognition",
                     "X-TC-Version": "2019-06-14"},
            json={
                "SecretId": settings.tencent_secret_id,
                "SecretKey": settings.tencent_secret_key,
                "ProjectId": 0,
                "SubServiceType": 2,
                "EngSerViceType": "16k_zh",
                "SourceType": 1,
                "VoiceFormat": audio_format,
                "Data": encoded,
                "DataLen": len(audio_bytes),
            },
        )
    return resp.json().get("Response", {}).get("Result", "")
