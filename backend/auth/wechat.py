import logging
import base64
import json
import httpx
from typing import Optional
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from config import settings

logger = logging.getLogger(__name__)

WECHAT_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"


async def exchange_code_for_session(code: str) -> dict:
    """用小程序 code 换取 session 信息，返回 {openid, session_key, unionid?}
    失败抛 ValueError("WeChat authentication failed")
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(WECHAT_CODE2SESSION_URL, params={
            "appid": settings.wechat_appid,
            "secret": settings.wechat_secret,
            "js_code": code,
            "grant_type": "authorization_code",
        })
    data = resp.json()
    if "errcode" in data and data["errcode"] != 0:
        logger.warning(
            "WeChat jscode2session failed: errcode=%s errmsg=%s",
            data.get("errcode"),
            data.get("errmsg"),
        )
        raise ValueError("WeChat authentication failed")
    return {
        "openid": data["openid"],
        "session_key": data["session_key"],
        "unionid": data.get("unionid"),
    }


def decrypt_user_data(encrypted_data: str, iv: str, session_key: str) -> dict:
    """使用 AES-CBC + PKCS7 解密微信加密用户数据，返回解密后的 JSON dict。
    encrypted_data、iv、session_key 均为 base64 编码字符串。
    """
    key = base64.b64decode(session_key)
    iv_bytes = base64.b64decode(iv)
    ciphertext = base64.b64decode(encrypted_data)

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv_bytes))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()

    unpadder = padding.PKCS7(128).unpadder()
    plaintext = unpadder.update(padded) + unpadder.finalize()

    return json.loads(plaintext)
