"""微信小程序订阅消息发送（日程提醒用）。

平台规则（一次性订阅消息）：
- 用户每授权一次 = 攒一条发送配额（勾过"总是保持以上选择"后授权静默通过）；
- 发送走 /cgi-bin/message/subscribe/send，需要小程序全局 access_token；
- errcode 43101 = 用户没有可用配额（拒收/没攒），上层应把本地配额清零。
"""
from __future__ import annotations
import json
import logging
import httpx
from config import settings
from redis_client import get_redis

logger = logging.getLogger(__name__)

_TOKEN_KEY = "wx:access_token"


async def get_access_token() -> str:
    """client_credential access_token，Redis 缓存（微信侧有效期 7200s，存 7000s）。
    注意：同一 appid 重复获取会让旧 token 在 5 分钟内失效，所以必须缓存共享。"""
    redis = await get_redis()
    cached = await redis.get(_TOKEN_KEY)
    if cached:
        return str(cached)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://api.weixin.qq.com/cgi-bin/token",
            params={
                "grant_type": "client_credential",
                "appid": settings.wechat_appid,
                "secret": settings.wechat_secret,
            },
        )
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"获取微信 access_token 失败: {data}")
    await redis.setex(_TOKEN_KEY, 7000, token)
    return token


async def list_subscribe_templates() -> list[dict]:
    """列出小程序已添加的订阅消息模板（含字段定义）——部署时探测字段 key 用。"""
    token = await get_access_token()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://api.weixin.qq.com/wxaapi/newtmpl/gettemplate",
            params={"access_token": token},
        )
    data = resp.json()
    if data.get("errcode") not in (0, None):
        raise RuntimeError(f"gettemplate 失败: {data}")
    return data.get("data", []) or []


# 模板字段（2026-06-12 用 list_subscribe_templates 实测校准，模板「日程提醒」）：
# 日程:{{thing1}} | 日程时间:{{time6}} | 提醒内容:{{thing9}} | 客户名称:{{thing11}} | 地点:{{thing3}}
# 订阅消息 data 必须与模板字段完全一致（缺字段/错 key 会 47003）。


def _clip(s: str, n: int = 20) -> str:
    """thing 类字段限 20 字符，超长直接被微信拒（47003），主动截断；空值用占位符。"""
    s = (s or "").strip() or "—"
    return s[: n - 1] + "…" if len(s) > n else s


async def send_schedule_reminder(
    openid: str,
    title: str,
    time_str: str,
    note: str = "",
    investor_name: str = "",
    location: str = "",
    page: str = "pages/calendar/index",
) -> dict:
    """发一条日程提醒。返回微信原始响应 {errcode, errmsg}；
    errcode==0 成功；43101 = 无配额（用户未订阅/已用完）。"""
    token = await get_access_token()
    payload = {
        "touser": openid,
        "template_id": settings.wx_schedule_tmpl_id,
        "page": page,
        "data": {
            "thing1": {"value": _clip(title)},
            "time6": {"value": time_str},
            "thing9": {"value": _clip(note or "日程即将开始")},
            "thing11": {"value": _clip(investor_name or "—")},
            "thing3": {"value": _clip(location or "—")},
        },
        "miniprogram_state": "formal",
        "lang": "zh_CN",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token={token}",
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    data = resp.json()
    logger.info("wx subscribe send openid=%s errcode=%s errmsg=%s",
                openid[:8] + "***", data.get("errcode"), data.get("errmsg"))
    return data
