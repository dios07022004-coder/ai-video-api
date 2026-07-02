"""Sanitization + key hashing tests."""

from __future__ import annotations

from app.security.keys import generate_api_key, hash_api_key, verify_api_key
from app.security.sanitize import safe_filename, sanitize_prompt


def test_sanitize_strips_control_chars() -> None:
    assert sanitize_prompt("hello\x00\x07 world") == "hello world"


def test_sanitize_collapses_and_caps() -> None:
    assert sanitize_prompt("a   b") == "a b"
    assert len(sanitize_prompt("x" * 10000, max_len=100)) == 100


def test_safe_filename_blocks_traversal() -> None:
    assert safe_filename("../../etc/passwd") == "passwd"
    assert "/" not in safe_filename("a/b/c.png")
    assert safe_filename("") == "file"


def test_api_key_roundtrip() -> None:
    key = generate_api_key()
    h = hash_api_key(key)
    assert verify_api_key(key, h)
    assert not verify_api_key("sk_wrong", h)
