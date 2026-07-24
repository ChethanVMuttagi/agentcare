"""Tests for the shared declarative base."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

from app.db.base import Base


def test_base_is_a_sqlalchemy_declarative_base() -> None:
    assert issubclass(Base, DeclarativeBase)


def test_base_metadata_has_no_tables_yet() -> None:
    # No domain model modules are registered yet — STORY-002 is
    # infrastructure only.
    assert list(Base.metadata.tables) == []
