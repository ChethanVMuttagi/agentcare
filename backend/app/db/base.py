"""Declarative base for all ORM models.

STORY-002 introduces only this infrastructure — no domain model modules
exist yet. When a future story adds one (e.g. `app/models/patient.py`),
it must subclass `Base` and be imported from `migrations/env.py` (see the
comment there) so Alembic's autogenerate can discover its table metadata.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models in AgentCare."""
