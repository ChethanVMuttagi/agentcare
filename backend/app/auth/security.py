"""Password hashing — never implemented by hand.

Uses `argon2-cffi`'s `PasswordHasher`, which defaults to Argon2id (the
password-hashing-competition-winning, OWASP-recommended variant) with a
random per-password salt generated and embedded by the library itself —
this module never handles salts directly. Plaintext passwords are never
persisted, logged, or otherwise stored anywhere by this module; only the
resulting hash string is ever returned.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(plain_password: str) -> str:
    """Hash `plain_password` with Argon2id. Returns the encoded hash
    string (algorithm, parameters, salt, and digest all embedded — this
    is what gets persisted as `User.password_hash`)."""
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Return True iff `plain_password` matches `password_hash`.

    Never raises: any verification failure (wrong password, or a
    malformed/foreign hash) returns False rather than propagating an
    exception — callers get a single, safe boolean either way.
    `InvalidHashError` (a malformed hash string — NOT a subclass of
    `VerificationError`, easy to miss) is caught explicitly alongside the
    expected `VerifyMismatchError`/`VerificationError`.
    """
    try:
        return _hasher.verify(password_hash, plain_password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
