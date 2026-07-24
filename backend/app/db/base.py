"""Declarative base for all ORM models.

Domain model modules live under `app/models/` and must subclass `Base`.
`migrations/env.py` imports the `app.models` package (which imports every
model module) so Alembic's autogenerate can discover their table metadata
— see `app/models/__init__.py`.

A naming convention is set on `Base.metadata` so constraints/indexes get
consistent, predictable names (e.g. `uq_organizations_slug`) instead of
driver-generated ones. This matters most for Alembic autogenerate, which
diffs against constraint names — without a convention, unrelated re-runs
can produce spurious diffs. Set once, here, before the first migration.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

_NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models in AgentCare."""

    metadata = MetaData(naming_convention=_NAMING_CONVENTION)
