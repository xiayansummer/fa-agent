from __future__ import annotations
import time
import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from auth.jwt import get_current_ir
from services import qiniu_service

router = APIRouter()

_PURPOSES = {
    "audio": ("audio/*;video/mp4", 200 * 1024 * 1024),  # 200MB
    "image": ("image/*", 20 * 1024 * 1024),             # 20MB
    "doc":   (None, 50 * 1024 * 1024),                  # 50MB
}


class UploadTokenRequest(BaseModel):
    purpose: str = Field("audio", description="audio | image | doc")
    filename: Optional[str] = Field(None, description="Original filename — used to keep extension")


class UploadTokenResponse(BaseModel):
    token: str
    key: str
    upload_url: str
    expires_at: int


@router.post("/token", response_model=UploadTokenResponse)
async def get_upload_token(
    body: UploadTokenRequest,
    current_ir: dict = Depends(get_current_ir),
):
    """Issue a Qiniu upload token. Frontend POSTs file directly to upload_url with this token."""
    if body.purpose not in _PURPOSES:
        raise HTTPException(status_code=400, detail=f"purpose must be one of {list(_PURPOSES)}")

    mime_limit, fsize_limit = _PURPOSES[body.purpose]
    ext = ""
    if body.filename and "." in body.filename:
        ext = "." + body.filename.rsplit(".", 1)[-1].lower()

    today = datetime.now().strftime("%Y%m%d")
    key = f"fa-agent/{body.purpose}/{today}/ir{current_ir['ir_id']}/{uuid.uuid4().hex}{ext}"

    try:
        result = qiniu_service.generate_upload_token(
            key=key,
            expires=3600,
            fsize_limit=fsize_limit,
            mime_limit=mime_limit,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return UploadTokenResponse(**result)


class SignResponse(BaseModel):
    url: str
    expires_at: int


@router.get("/sign", response_model=SignResponse)
async def sign_download(
    key: str = Query(..., description="Qiniu object key returned from upload"),
    expires: int = Query(3600, ge=60, le=86400, description="Seconds until URL expires"),
    _: dict = Depends(get_current_ir),
):
    """Generate a signed URL to download a private-bucket object."""
    try:
        url = qiniu_service.generate_download_url(key, expires=expires)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return SignResponse(url=url, expires_at=int(time.time()) + expires)
