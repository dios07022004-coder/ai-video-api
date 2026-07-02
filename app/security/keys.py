"""API key + admin token handling.

API keys are stored **hashed** (SHA-256). The plaintext is a high-entropy token
shown once at creation. Lookup uses a non-secret prefix for indexing and a
constant-time comparison on the full hash. Admin token comparison is also
constant-time to avoid timing oracles.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from app.config.settings import get_settings

_KEY_BYTES = 32  # 256 bits of entropy
_PREFIX_LEN = 8


def generate_api_key() -> str:
    """Return a fresh URL-safe API key (plaintext, show once)."""
    return "sk_" + secrets.token_urlsafe(_KEY_BYTES)


def key_prefix(plaintext: str) -> str:
    return plaintext[:_PREFIX_LEN]


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def verify_api_key(plaintext: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(plaintext), stored_hash)


def verify_admin_token(candidate: str | None) -> bool:
    if not candidate:
        return False
    expected = get_settings().admin_token
    return hmac.compare_digest(candidate, expected)
