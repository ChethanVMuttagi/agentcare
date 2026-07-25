"""Tests for app.auth.jwt (stateless access-token create/decode).

No database needed. `_configure_jwt_secret` (tests/auth/conftest.py)
supplies a synthetic JWT_SECRET_KEY for every test here.
"""

from __future__ import annotations

import uuid

import pytest

from app.auth.jwt import InvalidTokenError, create_access_token, decode_access_token
from app.core.config import Settings, get_settings


def test_create_and_decode_round_trip() -> None:
    settings = get_settings()
    user_id = uuid.uuid4()

    token = create_access_token(user_id, settings)
    claims = decode_access_token(token, settings)

    assert claims.sub == str(user_id)
    assert claims.jti  # present and non-empty
    assert claims.exp > claims.iat


def test_each_token_has_a_distinct_jti() -> None:
    settings = get_settings()
    user_id = uuid.uuid4()

    token_a = create_access_token(user_id, settings)
    token_b = create_access_token(user_id, settings)

    assert decode_access_token(token_a, settings).jti != decode_access_token(token_b, settings).jti


def test_expired_token_is_rejected() -> None:
    settings = get_settings()
    expired_settings = settings.model_copy(update={"jwt_access_token_expire_minutes": -1})
    token = create_access_token(uuid.uuid4(), expired_settings)

    with pytest.raises(InvalidTokenError):
        decode_access_token(token, expired_settings)


def test_malformed_token_is_rejected() -> None:
    settings = get_settings()
    with pytest.raises(InvalidTokenError):
        decode_access_token("this-is-not-a-jwt", settings)


def test_wrong_signature_is_rejected() -> None:
    settings = get_settings()
    token = create_access_token(uuid.uuid4(), settings)

    wrong_secret_settings = Settings(
        _env_file=None,
        jwt_secret_key="a-completely-different-synthetic-secret-value-32chars",
    )
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, wrong_secret_settings)


def test_create_access_token_requires_configured_secret() -> None:
    # Explicit `jwt_secret_key=None` overrides the autouse env var set by
    # tests/auth/conftest.py — constructor kwargs win over env vars in
    # pydantic-settings' precedence, so this genuinely tests "unconfigured".
    settings = Settings(_env_file=None, jwt_secret_key=None)
    with pytest.raises(RuntimeError):
        create_access_token(uuid.uuid4(), settings)
