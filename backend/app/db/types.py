"""Shared SQLAlchemy column-type helpers.

Currently just the `values_callable` every enum-backed column needs — see
`enum_values`.
"""

from __future__ import annotations

from enum import Enum


def enum_values(enum_cls: type[Enum]) -> list[str]:
    """`values_callable` for `sqlalchemy.Enum`.

    Without this, SQLAlchemy persists a Python `Enum`'s *member name*
    (e.g. ``"HOSPITAL"``) rather than its `.value` (e.g. ``"hospital"``) —
    a real footgun for `StrEnum`, whose whole point is that `.value` is
    the lowercase, API/JSON-friendly string. Passing this as
    `values_callable=enum_values` makes the persisted/CHECK-constrained
    database values match `.value` instead.
    """
    return [member.value for member in enum_cls]
