"""Database connectivity check used by the readiness endpoint.

Performs a real, lightweight query against the given engine — never a
faked result.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


async def check_database_connection(engine: AsyncEngine) -> bool:
    """Run `SELECT 1` against `engine`. Returns True/False, never raises.

    Connection/driver errors are logged server-side only. Callers must not
    forward exception details (which can include host/port information)
    to API clients — only this boolean result.

    Catches `SQLAlchemyError` (the usual case: SQLAlchemy wraps DBAPI
    errors in e.g. `OperationalError`) as well as `OSError`/`TimeoutError`,
    since some low-level connection failures (DNS resolution, connect
    timeouts) can surface from the driver before SQLAlchemy wraps them.
    This is a deliberate boundary — converting a real failure into an
    honest `False` (plus a server-side log), not into a fake success.
    """
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError):
        logger.exception("Database connectivity check failed.")
        return False
    return True
