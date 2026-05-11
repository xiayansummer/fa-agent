"""Unit tests for backend/services/crypto_service.py — no DB needed."""
from __future__ import annotations
import os
import sys

import pytest
from cryptography.fernet import Fernet

# Env stubs are set by conftest.py before any project imports.
# sys.path is also set there, but we insert here as well for safety when
# running this file standalone (pytest tests/test_crypto.py).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

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
