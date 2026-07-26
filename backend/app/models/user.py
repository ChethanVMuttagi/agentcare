"""User: AgentCare's global identity.

A User is deliberately NOT scoped to a single organization — a user may
belong to multiple organizations over time, so organization membership
(and the role that comes with it) is modeled separately by
`OrganizationMembership` (see `app/models/membership.py`), not as a
column on `User`. See docs/RBAC.md for the full identity/authorization
model.

Deliberately minimal identity fields — no date of birth, address, phone,
medical information, or other PII beyond an email address is stored here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.membership import OrganizationMembership
    from app.models.patient import Patient


def normalize_email(email: str) -> str:
    """Normalize an email address for storage and login lookup.

    Deliberately simple — not RFC-perfect canonicalization:

    - trims surrounding whitespace
    - lowercases the ENTIRE address, local part included

    Lowercasing the local part is technically looser than RFC 5321 (which
    permits a case-sensitive local part), but matches how virtually every
    real-world mail provider and SaaS product treats addresses in
    practice — and avoids user-facing login friction from a
    technically-correct-but-surprising case mismatch. This function is
    used identically for storing a user's email (`User._normalize_email`
    below) and for the login lookup (`app.auth.service.authenticate_user`)
    so the two can never silently diverge.
    """
    return email.strip().lower()


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """AgentCare's global identity. See docs/RBAC.md."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # Argon2id hash only (see app/auth/security.py) — plaintext is never
    # persisted, logged, or reachable from this column's name/type alone
    # implying otherwise.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # No delete cascade (see app/models/organization.py for the identical
    # rationale applied to Organization.facilities): an ORM
    # `session.delete(user)` must never silently cascade-delete their
    # memberships. The FK (`organization_memberships.user_id`, ON DELETE
    # RESTRICT) is the actual enforcement point.
    memberships: Mapped[list[OrganizationMembership]] = relationship(
        back_populates="user",
        passive_deletes=True,
    )

    # A User may be linked (Patient.user_id) from at most one Patient per
    # organization, but from multiple Patient rows across DIFFERENT
    # organizations (see app/models/patient.py) — hence a list here, same
    # shape as `memberships` above, not a single optional reference.
    patients: Mapped[list[Patient]] = relationship(
        back_populates="user",
        passive_deletes=True,
    )

    @validates("email")
    def _normalize_email(self, _key: str, value: str) -> str:
        return normalize_email(value)

    def __repr__(self) -> str:
        return f"User(id={self.id!r}, email={self.email!r})"
