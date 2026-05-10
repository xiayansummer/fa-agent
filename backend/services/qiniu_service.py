from __future__ import annotations
import time
from qiniu import Auth
from config import settings

_auth: Auth | None = None

# Region → frontend upload host (HTTPS, accelerated)
_UPLOAD_HOSTS = {
    "z0": "https://upload.qiniup.com",      # 华东-浙江
    "z1": "https://upload-z1.qiniup.com",   # 华北
    "z2": "https://upload-z2.qiniup.com",   # 华南
    "na0": "https://upload-na0.qiniup.com", # 北美
    "as0": "https://upload-as0.qiniup.com", # 东南亚
}


def _get_auth() -> Auth:
    global _auth
    if _auth is None:
        if not settings.qiniu_ak or not settings.qiniu_sk:
            raise RuntimeError("Qiniu AK/SK not configured")
        _auth = Auth(settings.qiniu_ak, settings.qiniu_sk)
    return _auth


def generate_upload_token(
    key: str,
    expires: int = 3600,
    fsize_limit: int = 100 * 1024 * 1024,
    mime_limit: str | None = None,
) -> dict:
    """Issue a one-time upload token for direct frontend → Qiniu upload."""
    auth = _get_auth()
    policy: dict = {
        "fsizeLimit": fsize_limit,
        "insertOnly": 1,  # forbid overwrite
    }
    if mime_limit:
        policy["mimeLimit"] = mime_limit
    token = auth.upload_token(settings.qiniu_bucket, key, expires, policy)
    return {
        "token": token,
        "key": key,
        "upload_url": _UPLOAD_HOSTS.get(settings.qiniu_region, "https://upload.qiniup.com"),
        "expires_at": int(time.time()) + expires,
    }


def generate_download_url(key: str, expires: int = 3600) -> str:
    """Sign a private-bucket download URL. Requires QINIU_DOMAIN (bound CNAME)."""
    if not settings.qiniu_domain:
        raise RuntimeError("QINIU_DOMAIN not configured — bind a CNAME to bucket first")
    auth = _get_auth()
    base = settings.qiniu_domain.rstrip("/")
    if not base.startswith("http"):
        base = f"https://{base}"
    return auth.private_download_url(f"{base}/{key}", expires=expires)
