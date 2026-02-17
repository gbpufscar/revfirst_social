"""Security helpers for password and API key handling."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets


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


def generate_api_key(prefix: str = "rfk") -> tuple[str, str, str]:
    """Generate API key and return (full_key, key_prefix, key_hash)."""

    short_prefix = secrets.token_hex(4)
    secret_part = secrets.token_urlsafe(32)
    full_key = f"{prefix}_{short_prefix}_{secret_part}"
    key_hash = hash_api_key(full_key)
    return full_key, short_prefix, key_hash
