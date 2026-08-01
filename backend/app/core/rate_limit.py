"""Rate limiting (Sprint 2): `slowapi` — a Starlette/FastAPI port of
Flask-Limiter, built on the `limits` package — guards the two endpoints
sensitive or expensive enough to need it: `POST /auth/token`
(credential-stuffing exposure) and `POST .../agent/execute` (each call
invokes a real, billed LLM provider). See
`app.api.v1.endpoints.auth`/`app.api.v1.endpoints.agent` for where
`@limiter.limit(...)` is actually applied, and
`app.core.exceptions.register_exception_handlers` for how an exceeded
limit is translated into this API's standard `ErrorResponse` shape.

`limiter` is a module-level singleton: `@limiter.limit(...)` decorators
run at IMPORT time (when the endpoint modules are first imported) — long
before any `Settings`/`FastAPI` app exists, so it cannot be constructed
inside `create_app()`. Its storage is in-memory (the `limits` package's
default) — this process is the only rate-limit bucket owner, the same
no-shared-cache posture the reminder engine already has ("the database
IS the queue" — see `app.workers.reminder_worker`); revisit with a
shared backend (e.g. Redis) before this ever runs horizontally scaled.

Each limit below is passed to `@limiter.limit(...)` as a CALLABLE, not a
fixed string, so `limits` re-invokes it — and therefore re-reads
`Settings` — on every request, rather than freezing whatever value
existed at decorator-evaluation (module import) time.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

limiter = Limiter(key_func=get_remote_address)


def auth_token_rate_limit() -> str:
    """`RATE_LIMIT_AUTH_TOKEN` (see `app.core.config.Settings`), read
    fresh on every request — see this module's docstring."""
    return get_settings().rate_limit_auth_token


def agent_execute_rate_limit() -> str:
    """`RATE_LIMIT_AGENT_EXECUTE` (see `app.core.config.Settings`) — same
    reasoning as `auth_token_rate_limit`."""
    return get_settings().rate_limit_agent_execute
