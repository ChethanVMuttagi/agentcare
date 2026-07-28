"""OrganizationMembership: associates a User with an Organization and a Role.

This is the join between global identity (`User`) and the tenant boundary
(`Organization`) — see docs/RBAC.md for the full identity/authorization
model and docs/adr/ADR-0004-identity-and-authorization.md for the
decision record.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, UniqueConstraint
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_values

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User


class Role(StrEnum):
    """A membership's role within its organization.

    Deliberately small (see docs/RBAC.md "Roles"). A role is ONE input
    into an authorization decision, not the complete decision — e.g.
    patient self-access rules (once a `Patient` entity exists) will
    further constrain what a `PATIENT`-role membership can reach, beyond
    what the role alone implies. Adding a role is a controlled schema +
    application change (see the `native_enum=False` note below), not a
    free-form string.
    """

    ADMIN = "admin"
    """Organization-level administrative access."""

    STAFF = "staff"
    """The healthcare organization's administrative/operational staff."""

    PATIENT = "patient"
    """Patient-facing access — will be constrained to the patient's own
    records/workflows once a `Patient` entity exists to constrain it to."""

    SUPERVISOR = "supervisor"
    """STORY-014: authorized, alongside `ADMIN`, to approve or reject a
    paused `ApprovalRequest` (see `app.models.approval`) — a narrower
    grant than `ADMIN`'s full organization-level access, for an
    organization that wants a dedicated human-in-the-loop reviewer role
    without granting full administrative rights."""


class OrganizationMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Associates one User with one Organization, with a Role.

    A user may hold at most one membership per organization (see the
    unique constraint below), but may belong to multiple organizations
    via multiple memberships — this is why `organization_id` does not
    live directly on `User`. See docs/RBAC.md.
    """

    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_organization_memberships_organization_id_user_id",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    role: Mapped[Role] = mapped_column(
        # native_enum=False + create_constraint=True for the same reason
        # as Organization.organization_type — see app/models/organization.py.
        SqlEnum(
            Role,
            name="membership_role",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=32,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")

    def __repr__(self) -> str:
        return (
            f"OrganizationMembership(id={self.id!r}, "
            f"organization_id={self.organization_id!r}, "
            f"user_id={self.user_id!r}, role={self.role!r})"
        )
