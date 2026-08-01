"""`app.core.rate_limit` — the settings-reading helpers
`@limiter.limit(...)` is given (see `app.api.v1.endpoints.auth`/`agent`),
NOT the actual over-the-limit-returns-429 behavior (that requires a real
HTTP request through the decorated route — see
`tests/api/test_rate_limiting.py`). Confirms these helpers genuinely
read from `Settings` on every call, rather than freezing a value.
"""

from __future__ import annotations

import pytest

from app.core import rate_limit
from app.core.config import Settings


def test_auth_token_rate_limit_reads_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    custom_settings = Settings(_env_file=None, rate_limit_auth_token="2/second")
    monkeypatch.setattr(rate_limit, "get_settings", lambda: custom_settings)

    assert rate_limit.auth_token_rate_limit() == "2/second"


def test_agent_execute_rate_limit_reads_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    custom_settings = Settings(_env_file=None, rate_limit_agent_execute="3/hour")
    monkeypatch.setattr(rate_limit, "get_settings", lambda: custom_settings)

    assert rate_limit.agent_execute_rate_limit() == "3/hour"


def test_limiter_is_a_shared_module_level_singleton() -> None:
    """`@limiter.limit(...)` decorators (see `app.api.v1.endpoints.auth`/
    `agent`) close over this exact object at import time — re-importing
    the module must yield the SAME instance, not a fresh one, or those
    decorators and `tests.conftest._reset_rate_limiter` would be
    resetting/checking two different limiters."""
    from app.core.rate_limit import limiter as limiter_again

    assert rate_limit.limiter is limiter_again
