"""Fixtures shared by tests/auth/.

Every test in this package needs a configured `JWT_SECRET_KEY` to create
or decode tokens — set here as an obviously-synthetic value, autoused so
individual test files don't need to repeat it. Deliberately scoped to
this directory only: `tests/core/test_config.py` specifically tests
`Settings`' behavior when `jwt_secret_key` is *not* set, and a
root-level autouse fixture would break that.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.config import get_settings

# 32+ chars so PyJWT doesn't emit its "InsecureKeyLengthWarning" — still
# obviously synthetic, never used outside this test suite.
SYNTHETIC_JWT_SECRET = "synthetic-test-jwt-secret-do-not-use-in-production-32chars"


@pytest.fixture(autouse=True)
def _configure_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET_KEY", SYNTHETIC_JWT_SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
