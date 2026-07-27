"""Domain model package.

Importing this package imports every model module, which registers their
table metadata on `app.db.base.Base.metadata` — the single thing
`migrations/env.py` needs for Alembic's `--autogenerate` to discover the
current schema. Add new model modules' imports here as they're introduced
so this stays the one place that needs updating, instead of a growing
list of import comments in `migrations/env.py` itself.
"""

from app.models.appointment import Appointment, AppointmentStatus
from app.models.department import Department
from app.models.facility import Facility, FacilityType
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization, OrganizationType
from app.models.patient import Patient
from app.models.patient_document import (
    DocumentMediaType,
    DocumentStatus,
    DocumentType,
    PatientDocument,
)
from app.models.practitioner import Practitioner, PractitionerType
from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability
from app.models.practitioner_department import PractitionerDepartment
from app.models.user import User

__all__ = [
    "Appointment",
    "AppointmentStatus",
    "DayOfWeek",
    "Department",
    "DocumentMediaType",
    "DocumentStatus",
    "DocumentType",
    "Facility",
    "FacilityType",
    "Organization",
    "OrganizationMembership",
    "OrganizationType",
    "Patient",
    "PatientDocument",
    "Practitioner",
    "PractitionerAvailability",
    "PractitionerDepartment",
    "PractitionerType",
    "Role",
    "User",
]
