"""Domain model package.

Importing this package imports every model module, which registers their
table metadata on `app.db.base.Base.metadata` — the single thing
`migrations/env.py` needs for Alembic's `--autogenerate` to discover the
current schema. Add new model modules' imports here as they're introduced
so this stays the one place that needs updating, instead of a growing
list of import comments in `migrations/env.py` itself.
"""

from app.models.facility import Facility, FacilityType
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization, OrganizationType
from app.models.user import User

__all__ = [
    "Facility",
    "FacilityType",
    "Organization",
    "OrganizationMembership",
    "OrganizationType",
    "Role",
    "User",
]
