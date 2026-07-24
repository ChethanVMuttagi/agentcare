"""Shared fixtures for database-layer tests.

`get_settings`, `get_engine`, and `get_sessionmaker` are process-wide
caches (see app/core/config.py and app/db/session.py). Tests in this
package manipulate them via monkeypatched environment variables, so each
test gets a clean cache before and after running.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.core.config import get_settings
from app.db.session import dispose_engine, get_engine, get_sessionmaker


@pytest.fixture(autouse=True)
async def _clear_caches() -> AsyncIterator[None]:
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    yield
    await dispose_engine()
    get_settings.cache_clear()
