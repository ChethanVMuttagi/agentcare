"""Organization: AgentCare's tenant boundary.

See docs/DOMAIN_MODEL.md for the full tenant-hierarchy explanation and
docs/adr/ADR-0003-multi-tenancy.md for the tenancy decision record.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_values

if TYPE_CHECKING:
    from app.models.facility import Facility


class OrganizationType(StrEnum):
    """The kind of customer/tenant this organization represents.

    Deliberately small and specific to AgentCare's current scope (see
    docs/DOMAIN_MODEL.md). Adding a value is a controlled schema +
    application change — a new Alembic migration adjusting the persisted
    check constraint (see the `native_enum=False` note on the mapped
    column below) plus an application release — not a free-form string
    field a caller can set to anything.
    """

    HOSPITAL = "hospital"
    CLINIC = "clinic"
    HOSPITAL_GROUP = "hospital_group"
    HEALTHCARE_PROVIDER = "healthcare_provider"


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """AgentCare's tenant boundary.

    Every future tenant-owned entity is expected to carry `organization_id`
    either directly or through an explicit ownership path — see
    docs/DOMAIN_MODEL.md and ADR-0003 for what this story does and does
    not enforce about that.
    """

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    organization_type: Mapped[OrganizationType] = mapped_column(
        # native_enum=False + create_constraint=True: persisted as VARCHAR
        # with a real database CHECK constraint, rather than a native
        # PostgreSQL ENUM type — specifically so that adding a future value
        # is an ordinary constraint-altering migration instead of the more
        # awkward `ALTER TYPE ... ADD VALUE` semantics native PG enums
        # require. (create_constraint defaults to False in SQLAlchemy 2.x
        # when native_enum=False — it must be set explicitly to get real
        # DB-level enforcement, not just application-side validation.)
        # See docs/DOMAIN_MODEL.md "Enum Strategy".
        SqlEnum(
            OrganizationType,
            name="organization_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=32,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Default relationship cascade ("save-update, merge") deliberately does
    # NOT include "delete" or "delete-orphan": an ORM `session.delete(org)`
    # must never silently cascade-delete every facility. The database FK
    # (`facilities.organization_id`, ON DELETE RESTRICT — see
    # app/models/facility.py) is the actual enforcement point;
    # `passive_deletes=True` tells SQLAlchemy to rely on that DB behavior
    # rather than loading and manipulating the whole collection itself.
    facilities: Mapped[list[Facility]] = relationship(
        back_populates="organization",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"Organization(id={self.id!r}, slug={self.slug!r})"
