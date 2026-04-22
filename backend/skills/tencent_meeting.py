import json
import hmac
import hashlib
import time
import httpx
from harness.skill_registry import skill, skill_registry
from config import settings

BASE_URL = "https://api.meeting.qq.com/v1"

def _sign_request(method: str, path: str, body: str) -> dict:
    timestamp = str(int(time.time()))
    nonce = "12345"
    to_sign = f"{settings.tencent_meeting_secret_id}\n{timestamp}\n{nonce}\n{method}\n{path}\n{body}"
    signature = hmac.new(
        settings.tencent_meeting_secret_key.encode(),
        to_sign.encode(), hashlib.sha256
    ).hexdigest()
    return {
        "AppId": settings.tencent_meeting_app_id,
        "SecretId": settings.tencent_meeting_secret_id,
        "Timestamp": timestamp,
        "Nonce": nonce,
        "Signature": signature,
    }

@skill(registry=skill_registry, name="腾讯会议.预约",
       version="1.0", timeout=10, retry=1)
async def tencent_book_meeting(title: str, start_time: int,
                                end_time: int, user_id: str) -> str:
    path = "/meetings"
    body_dict = {"userid": user_id, "instanceid": 1,
                 "subject": title, "type": 0,
                 "start_time": str(start_time), "end_time": str(end_time)}
    body = json.dumps(body_dict)
    headers = _sign_request("POST", path, body)
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{BASE_URL}{path}", headers=headers, content=body)
    return resp.json().get("meeting_info_list", [{}])[0].get("meeting_id", "")

@skill(registry=skill_registry, name="腾讯会议.获取转录",
       version="1.0", timeout=30, retry=1, fallback="")
async def tencent_get_transcript(meeting_id: str) -> str:
    path = f"/meetings/{meeting_id}/transcripts"
    headers = _sign_request("GET", path, "")
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE_URL}{path}", headers=headers)
    transcripts = resp.json().get("transcripts", [])
    return "\n".join(t.get("content", "") for t in transcripts)
