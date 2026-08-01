"""Property-based tests (Sprint 3, `hypothesis`) for the pagination
`limit` bound (`app.core.pagination.MAX_PAGE_SIZE`) — complements the
fixed boundary cases in `tests/api/test_pagination_validation.py`
(which exercise it end-to-end, over real HTTP, for every list endpoint)
by checking the underlying constraint's arithmetic holds for ARBITRARY
integers, not just 0/1/MAX/MAX+1.

Tests the same `ge=1, le=MAX_PAGE_SIZE` constraint the real endpoints'
`Query(...)` annotations use (see e.g.
`app.api.v1.endpoints.workflows.list_workflows`) via a plain Pydantic
`TypeAdapter` — `fastapi.Query` is itself built on the same Pydantic
`Field` constraint machinery, so this proves the BOUND's own arithmetic
is correct without needing a live HTTP round trip (and therefore a
database + authenticated caller) per generated example.
"""

from __future__ import annotations

from typing import Annotated

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import Field, TypeAdapter, ValidationError

from app.core.pagination import MAX_PAGE_SIZE

_LimitAdapter = TypeAdapter(Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE)])


@given(value=st.integers(min_value=1, max_value=MAX_PAGE_SIZE))
def test_any_limit_within_bounds_is_accepted(value: int) -> None:
    assert _LimitAdapter.validate_python(value) == value


@given(
    value=st.integers(min_value=-1_000_000, max_value=1_000_000).filter(
        lambda v: v < 1 or v > MAX_PAGE_SIZE
    )
)
def test_any_limit_outside_bounds_is_rejected(value: int) -> None:
    with pytest.raises(ValidationError):
        _LimitAdapter.validate_python(value)
