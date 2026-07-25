"""Tests for app.auth.security (password hashing).

No database needed — these are pure, synchronous unit tests.
"""

from __future__ import annotations

from app.auth.security import hash_password, verify_password

_SYNTHETIC_PASSWORD = "Synthetic-Test-Password-123!"


def test_hash_differs_from_plaintext() -> None:
    hashed = hash_password(_SYNTHETIC_PASSWORD)
    assert hashed != _SYNTHETIC_PASSWORD


def test_hash_is_argon2id() -> None:
    hashed = hash_password(_SYNTHETIC_PASSWORD)
    assert hashed.startswith("$argon2id$")


def test_verify_accepts_correct_password() -> None:
    hashed = hash_password(_SYNTHETIC_PASSWORD)
    assert verify_password(_SYNTHETIC_PASSWORD, hashed) is True


def test_verify_rejects_wrong_password() -> None:
    hashed = hash_password(_SYNTHETIC_PASSWORD)
    assert verify_password("a-completely-different-password", hashed) is False


def test_same_password_produces_different_hashes() -> None:
    # Each call gets its own random salt (handled internally by
    # argon2-cffi) — two hashes of the same password must never match
    # byte-for-byte, even though both verify correctly.
    first = hash_password(_SYNTHETIC_PASSWORD)
    second = hash_password(_SYNTHETIC_PASSWORD)
    assert first != second
    assert verify_password(_SYNTHETIC_PASSWORD, first) is True
    assert verify_password(_SYNTHETIC_PASSWORD, second) is True


def test_verify_fails_safely_against_a_malformed_hash() -> None:
    # Must return False, never raise — see app/auth/security.py for why
    # InvalidHashError needs its own except clause.
    assert verify_password(_SYNTHETIC_PASSWORD, "not-a-real-argon2-hash") is False
