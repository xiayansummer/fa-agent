from __future__ import annotations
from cryptography.fernet import Fernet, InvalidToken
from config import settings

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(settings.token_encrypt_key.encode())
    return _fernet


def encrypt(plaintext: str) -> bytes:
    """Encrypt a string, returns Fernet token bytes (safe to store as BLOB)."""
    return _get_fernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    """Decrypt Fernet token bytes back to plaintext."""
    try:
        return _get_fernet().decrypt(ciphertext).decode()
    except InvalidToken as e:
        raise ValueError("token decryption failed — key mismatch?") from e
