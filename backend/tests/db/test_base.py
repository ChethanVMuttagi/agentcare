"""Tests for the shared declarative base."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

from app.db.base import Base


def test_base_is_a_sqlalchemy_declarative_base() -> None:
    assert issubclass(Base, DeclarativeBase)


def test_base_metadata_registers_domain_model_tables() -> None:
    # `Base.metadata` is a single, process-wide object: importing
    # `app.models` (done by tests/models/) registers `organizations` and
    # `facilities` on it. Since STORY-003, that's the expected state, not
    # emptiness — see docs/DOMAIN_MODEL.md.
    from app import models  # noqa: F401  (ensures registration for this test)

    assert {"organizations", "facilities"}.issubset(Base.metadata.tables)
