import logging
import httpx
from config import settings

logger = logging.getLogger(__name__)

WECHAT_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"

async def exchange_code_for_openid(code: str) -> str:
    """用小程序 code 换取 openid，失败抛 ValueError"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(WECHAT_CODE2SESSION_URL, params={
            "appid": settings.wechat_appid,
            "secret": settings.wechat_secret,
            "js_code": code,
            "grant_type": "authorization_code",
        })
    data = resp.json()
    if "errcode" in data and data["errcode"] != 0:
        logger.warning("WeChat jscode2session failed: errcode=%s errmsg=%s", data.get("errcode"), data.get("errmsg"))
        raise ValueError("WeChat authentication failed")
    return data["openid"]
