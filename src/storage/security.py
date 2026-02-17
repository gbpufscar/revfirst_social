"""Security helpers for password and API key handling."""

from __future__ import annotations

import base64
from functools import lru_cache
import hashlib
import hmac
import os
import secrets
from typing import Tuple

from src.core.config import get_settings


PBKDF2_ROUNDS = 260_000


def hash_password(password: str) -> str:
    """Hash password with PBKDF2-SHA256 and a random salt."""

    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    digest_b64 = base64.b64encode(digest).decode("ascii")
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt_b64}${digest_b64}"


def verify_password(password: str, encoded_hash: str) -> bool:
    """Verify password against a PBKDF2-SHA256 encoded hash."""

    try:
        algorithm, rounds_str, salt_b64, digest_b64 = encoded_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        rounds = int(rounds_str)
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(digest_b64.encode("ascii"))
    except Exception:
        return False

    observed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(observed, expected)


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def hash_token(secret_value: str) -> str:
    return hashlib.sha256(secret_value.encode("utf-8")).hexdigest()


def generate_api_key(prefix: str = "rfk") -> Tuple[str, str, str]:
    """Generate API key and return (full_key, key_prefix, key_hash)."""

    short_prefix = secrets.token_hex(4)
    secret_part = secrets.token_urlsafe(32)
    full_key = f"{prefix}_{short_prefix}_{secret_part}"
    key_hash = hash_api_key(full_key)
    return full_key, short_prefix, key_hash


def _derive_key(material: str) -> bytes:
    return hashlib.sha256(material.encode("utf-8")).digest()


@lru_cache(maxsize=1)
def get_token_key() -> bytes:
    settings = get_settings()
    seed = settings.token_encryption_key.strip() or settings.secret_key or "revfirst-dev-token-key"
    return _derive_key(seed)


def _xor_bytes(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right))


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    blocks = []
    counter = 0
    while len(b"".join(blocks)) < length:
        block = hmac.new(
            key,
            nonce + counter.to_bytes(4, "big"),
            digestmod=hashlib.sha256,
        ).digest()
        blocks.append(block)
        counter += 1
    return b"".join(blocks)[:length]


def encrypt_token(secret_value: str) -> str:
    key = get_token_key()
    nonce = os.urandom(16)
    plaintext = secret_value.encode("utf-8")
    stream = _keystream(key, nonce, len(plaintext))
    ciphertext = _xor_bytes(plaintext, stream)
    mac = hmac.new(key, nonce + ciphertext, digestmod=hashlib.sha256).digest()
    blob = nonce + mac + ciphertext
    return base64.urlsafe_b64encode(blob).decode("ascii")


def decrypt_token(ciphertext: str) -> str:
    key = get_token_key()
    try:
        blob = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
    except Exception as exc:
        raise ValueError("Invalid encrypted token payload") from exc

    if len(blob) < 48:
        raise ValueError("Invalid encrypted token payload")
    nonce = blob[:16]
    mac = blob[16:48]
    encrypted = blob[48:]
    expected_mac = hmac.new(key, nonce + encrypted, digestmod=hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected_mac):
        raise ValueError("Invalid encrypted token payload")

    stream = _keystream(key, nonce, len(encrypted))
    plaintext = _xor_bytes(encrypted, stream)
    return plaintext.decode("utf-8")
