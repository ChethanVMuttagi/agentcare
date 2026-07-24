"""Reusable persistence mixins for SQLAlchemy 2.x ORM models.

Introduced now that real domain models (Organization, Facility — see
`app/models/`) exist, not speculatively ahead of them. Both mixins are
plain Python mixins (no metaclass magic): a model combines them with
`Base` via multiple inheritance, e.g. `class Organization(UUIDPrimaryKeyMixin,
TimestampMixin, Base): ...`.

`is_active` (defined per-model, not here) is a business-status flag, not
soft deletion — no soft-delete behavior is implemented anywhere in this
mixin or elsewhere.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Uuid
from sqlalchemy.orm import Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UUIDPrimaryKeyMixin:
    """Adds an application-generated UUID primary key.

    Generated in Python (`uuid.uuid4`), not by the database
    (e.g. Postgres `gen_random_uuid()`) — this is portable across
    database backends and lets the id be known before the row is
    inserted, with no compelling PostgreSQL-specific reason to prefer a
    server-generated id for this story's needs.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    """Adds timezone-aware `created_at`/`updated_at` timestamps.

    Both are stored as `timestamptz` (`DateTime(timezone=True)`) and set
    in Python (UTC), not via a database default/trigger. `updated_at`'s
    `onupdate` only fires for updates made through this ORM's own
    `Session` — a raw SQL `UPDATE` bypassing the ORM would not refresh
    it. That's an accepted limitation for this story; a database-level
    trigger would close that gap but is more infrastructure than this
    foundational story needs.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )
