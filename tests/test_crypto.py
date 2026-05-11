"""Unit tests for backend/services/crypto_service.py — no DB needed."""
from __future__ import annotations
import os
import sys

import pytest
from cryptography.fernet import Fernet

# ---------------------------------------------------------------------------
# Bootstrap: add backend to sys.path BEFORE importing any backend modules.
# Env vars are set here as fallback stubs in case a .env file is absent
# (e.g. CI). pydantic-settings prefers .env over os.environ so a real .env
# on disk takes precedence; these setdefault calls never overwrite it.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

_REQUIRED_STUBS = {
    "TOKEN_ENCRYPT_KEY": Fernet.generate_key().decode(),
    "MYSQL_URL": "mysql+aiomysql://x:x@localhost/x",
    "REDIS_URL": "redis://localhost/0",
    "WECHAT_APPID": "wx_stub",
    "WECHAT_SECRET": "stub",
    "JWT_SECRET_KEY": "stub-jwt-secret-that-is-long-enough",
    "AI_API_KEY": "sk-stub",
    "TAVILY_API_KEY": "tvly-stub",
    "QMINGPIAN_TOKEN": "stub",
    "TENCENT_SECRET_ID": "stub",
    "TENCENT_SECRET_KEY": "stub",
    "TENCENT_MEETING_APP_ID": "stub",
    "TENCENT_MEETING_SECRET_ID": "stub",
    "TENCENT_MEETING_SECRET_KEY": "stub",
}
for _k, _v in _REQUIRED_STUBS.items():
    os.environ.setdefault(_k, _v)

# Now safe to import — settings will pick up values from .env or os.environ.
from services.crypto_service import encrypt, decrypt  # noqa: E402


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_roundtrip():
    """encrypt then decrypt returns the original plaintext."""
    original = "super-secret-access-token-abc123"
    ciphertext = encrypt(original)
    assert isinstance(ciphertext, bytes)
    assert decrypt(ciphertext) == original


def test_wrong_key_raises():
    """Bytes encrypted by a *different* Fernet key cannot be decrypted — raises ValueError."""
    # Encrypt with a completely separate Fernet instance (different key).
    wrong_fernet = Fernet(Fernet.generate_key())
    ciphertext_from_wrong_key = wrong_fernet.encrypt(b"some-value")

    # Our decrypt() should reject ciphertext it didn't produce.
    with pytest.raises(ValueError, match="token decryption failed"):
        decrypt(ciphertext_from_wrong_key)


def test_empty_string():
    """encrypt('') then decrypt returns ''."""
    assert decrypt(encrypt("")) == ""


def test_unicode():
    """encrypt/decrypt handles multi-byte unicode (CJK) correctly."""
    assert decrypt(encrypt("张三")) == "张三"
