"""AppointmentService: booking, rescheduling, and cancellation.

Follows the `Route -> Service -> Repository -> Session` pattern
established in STORY-005/006. This is the ONLY place appointment state
transitions happen — routes never set `Appointment.status` directly, and
`app.repositories.appointment` never decides whether a transition is
allowed (see `_ensure_transition_allowed`).

## Transaction & Conflict Handling

`book_appointment` and `reschedule_appointment` do NOT pre-check for a
conflicting time by querying existing appointments first — that
select-then-insert shape is exactly the race-prone pattern this story
explicitly rejects (see docs/adr/ADR-0007-appointment-concurrency.md).
Instead, both methods always attempt the write and let PostgreSQL's
EXCLUDE constraints (`app/models/appointment.py`) be the sole arbiter of
whether a time is actually free at the instant of commit. The specific,
expected `IntegrityError` that constraint raises is caught, the
transaction is rolled back (so the session is never left in a failed-
transaction state), and it is translated into `AppointmentConflictError`
(409). Any OTHER `IntegrityError` — one this service did not specifically
anticipate — is deliberately left to propagate uncaught, exactly like
every other service in this codebase (see `app.services.patient`'s module
docstring): it becomes an unhandled 500 rather than being mislabeled as a
scheduling conflict.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.appointment import Appointment, AppointmentStatus
from app.repositories import appointment as appointment_repository
from app.repositories import department as department_repository
from app.repositories import patient as patient_repository
from app.repositories import practitioner as practitioner_repository
from app.repositories import practitioner_department as practitioner_department_repository
from app.services.availability_query import (
    MAX_APPOINTMENT_DURATION_MINUTES,
    MIN_APPOINTMENT_DURATION_MINUTES,
    AvailabilityQueryService,
)
from app.services.reminder_scheduler import ReminderScheduler

logger = logging.getLogger("agentcare.services.appointment")

# PostgreSQL's SQLSTATE for "exclusion_violation" — the ONLY way an
# `IntegrityError` can carry this code is a violated `EXCLUDE` constraint,
# and `ex_appointments_practitioner_no_overlap`/
# `ex_appointments_patient_no_overlap` (`app/models/appointment.py`) are
# the ONLY exclusion constraints anywhere in this schema. Checked via
# `exc.orig.sqlstate` rather than a constraint-name attribute: SQLAlchemy's
# asyncpg dialect wraps the raw `asyncpg.exceptions.ExclusionViolationError`
# in its own DBAPI-emulation exception, which preserves `.sqlstate` but
# does NOT preserve `.constraint_name` — verified against real PostgreSQL
# (`tests/db/test_appointment_concurrency.py`). Any OTHER SQLSTATE reaching
# `_translate_integrity_error` is NOT this, and is re-raised unhandled, by
# design — see the module docstring.
_EXCLUSION_VIOLATION_SQLSTATE = "23P01"


class AppointmentNotFoundError(AppException):
    """404: no appointment matches, within the caller's own organization
    (and, if scoped to a patient, within that patient's own appointments).

    Deliberately raised identically whether the appointment truly doesn't
    exist, belongs to a different organization, or belongs to a different
    patient within the SAME organization — a `PATIENT` caller who
    supplies another patient's appointment id learns nothing more than
    "not found". See docs/APPOINTMENTS.md "Cross-Tenant & Patient
    Isolation".
    """

    status_code = 404
    error_code = "appointment_not_found"


class PatientInactiveError(AppException):
    """422: the patient exists but is not active."""

    status_code = 422
    error_code = "patient_inactive"


class PractitionerInactiveError(AppException):
    """422: the practitioner exists but is not active."""

    status_code = 422
    error_code = "practitioner_inactive"


class DepartmentInactiveError(AppException):
    """422: the department exists but is not active."""

    status_code = 422
    error_code = "department_inactive"


class PractitionerNotAssignedError(AppException):
    """422: no ACTIVE practitioner-department assignment exists.

    Same meaning/status as `app.services.availability.PractitionerNotAssignedError`
    — redefined here (rather than imported) so this service does not
    depend on the availability-WINDOW-management service module for an
    unrelated reason; both independently express "this practitioner is
    not currently assigned to this department".
    """

    status_code = 422
    error_code = "practitioner_not_assigned"


class InvalidAppointmentDurationError(AppException):
    """422: `duration_minutes` is outside the administrative bounds."""

    status_code = 422
    error_code = "invalid_appointment_duration"


class AppointmentInPastError(AppException):
    """422: the requested start time is not in the future."""

    status_code = 422
    error_code = "appointment_in_past"


class AppointmentOutsideAvailabilityError(AppException):
    """422: the requested time does not fall entirely within an active
    recurring availability window for this practitioner/department."""

    status_code = 422
    error_code = "appointment_outside_availability"


class AppointmentConflictError(AppException):
    """409: this time is no longer available — translated from the
    PostgreSQL EXCLUDE constraint that actually enforces this, never a
    raw database error. See the module docstring."""

    status_code = 409
    error_code = "appointment_conflict"


class InvalidAppointmentTransitionError(AppException):
    """422: this status transition is not allowed (e.g. rescheduling or
    cancelling an appointment that isn't currently `booked`)."""

    status_code = 422
    error_code = "invalid_appointment_transition"


def _validate_duration(duration_minutes: int) -> None:
    too_short = duration_minutes < MIN_APPOINTMENT_DURATION_MINUTES
    too_long = duration_minutes > MAX_APPOINTMENT_DURATION_MINUTES
    if too_short or too_long:
        raise InvalidAppointmentDurationError(
            "duration_minutes must be between "
            f"{MIN_APPOINTMENT_DURATION_MINUTES} and {MAX_APPOINTMENT_DURATION_MINUTES}."
        )


def _translate_integrity_error(exc: IntegrityError) -> AppointmentConflictError | None:
    """Return an `AppointmentConflictError` IF `exc` is the known
    appointment EXCLUDE-constraint violation, else `None` — in which case
    the caller re-raises `exc` unchanged."""
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == _EXCLUSION_VIOLATION_SQLSTATE:
        return AppointmentConflictError(
            "This time is no longer available for this practitioner or patient."
        )
    return None


class AppointmentService:
    """Appointment booking/rescheduling/cancellation, scoped to one `AsyncSession`.

    ## Automatic Reminder Scheduling (STORY-013)

    `book_appointment`/`reschedule_appointment`/`cancel_appointment` each
    accept an OPTIONAL `initiated_by_user_id`. When given, the
    appointment mutation's own commit is followed by a SEPARATE,
    independently-committed call into `ReminderScheduler` — the same
    multi-step-with-independent-commits pattern
    `app.ai.orchestration.AgentOrchestrationService` already established
    for a sequence of durable steps, not one large cross-service
    transaction (see docs/adr/ADR-0012-reminder-engine.md "Atomicity").
    When omitted (the default — every pre-STORY-013 caller/test), NO
    reminder is scheduled/cancelled at all: this keeps every existing
    call site's behavior completely unchanged. The real product surface
    (`app.api.v1.endpoints.appointments`, `app.ai.tools.appointment_tools`)
    ALWAYS supplies it — see those modules.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._availability = AvailabilityQueryService(session)
        self._reminder_scheduler = ReminderScheduler(session)

    async def _schedule_reminder_best_effort(
        self, appointment: Appointment, *, initiated_by_user_id: uuid.UUID
    ) -> None:
        """The appointment mutation ABOVE this call already committed —
        it genuinely succeeded. A reminder-scheduling failure must never
        make a genuinely successful booking/reschedule APPEAR to have
        failed to a caller. Logged, never raised — mirrors
        `app.ai.tools.registry.ToolRegistry.execute`'s identical
        "never let a downstream failure corrupt an already-true result"
        philosophy.

        `AppException` (a controlled business-rule rejection — e.g. the
        initiator's membership is no longer active — always raised
        BEFORE any flush anywhere in `ReminderService`/`WorkflowService`)
        needs no rollback: the session is already clean. Any OTHER
        exception is treated as a genuine, unexpected DB-level failure
        that MAY have left a flushed-but-uncommitted change behind, so
        `self._session` is rolled back to remain usable for whatever the
        caller does next.
        """
        try:
            await self._reminder_scheduler.schedule_appointment_reminder(
                appointment, initiated_by_user_id=initiated_by_user_id
            )
        except AppException:
            logger.exception(
                "failed to schedule reminder for appointment %s; appointment itself is unaffected",
                appointment.id,
            )
        except Exception:
            await self._session.rollback()
            logger.exception(
                "failed to schedule reminder for appointment %s; appointment itself is unaffected",
                appointment.id,
            )

    async def _cancel_reminders_best_effort(
        self, appointment: Appointment, *, initiated_by_user_id: uuid.UUID
    ) -> None:
        """Same reasoning as `_schedule_reminder_best_effort`, for the
        cancellation side."""
        try:
            await self._reminder_scheduler.cancel_appointment_reminders(
                appointment, initiated_by_user_id=initiated_by_user_id
            )
        except AppException:
            logger.exception(
                "failed to cancel reminders for appointment %s; appointment itself is unaffected",
                appointment.id,
            )
        except Exception:
            await self._session.rollback()
            logger.exception(
                "failed to cancel reminders for appointment %s; appointment itself is unaffected",
                appointment.id,
            )

    async def _ensure_bookable(
        self,
        *,
        organization_id: uuid.UUID,
        patient_id: uuid.UUID,
        practitioner_id: uuid.UUID,
        department_id: uuid.UUID,
        start_at: datetime,
        end_at: datetime,
    ) -> None:
        """Full business-rule revalidation shared by `book_appointment` and
        `reschedule_appointment`. Never trusts that a caller who
        previously queried available times still describes a valid,
        current booking — every check here is re-run fresh. See the
        module and package docstrings."""
        patient = await patient_repository.get_by_id(
            self._session, organization_id=organization_id, patient_id=patient_id
        )
        if patient is None:
            raise AppointmentNotFoundError("No patient found with this id in this organization.")
        if not patient.is_active:
            raise PatientInactiveError("This patient is not active.")

        practitioner = await practitioner_repository.get_by_id(
            self._session, organization_id=organization_id, practitioner_id=practitioner_id
        )
        if practitioner is None:
            raise AppointmentNotFoundError(
                "No practitioner found with this id in this organization."
            )
        if not practitioner.is_active:
            raise PractitionerInactiveError("This practitioner is not active.")

        department = await department_repository.get_by_id(
            self._session, organization_id=organization_id, department_id=department_id
        )
        if department is None:
            raise AppointmentNotFoundError(
                "No department found with this id in this organization."
            )
        if not department.is_active:
            raise DepartmentInactiveError("This department is not active.")

        assignment = await practitioner_department_repository.get_assignment(
            self._session,
            organization_id=organization_id,
            practitioner_id=practitioner_id,
            department_id=department_id,
        )
        if assignment is None or not assignment.is_active:
            raise PractitionerNotAssignedError(
                "This practitioner is not actively assigned to this department."
            )

        if start_at <= datetime.now(UTC):
            raise AppointmentInPastError("The requested start time must be in the future.")

        fits = await self._availability.is_within_availability(
            organization_id=organization_id,
            practitioner_id=practitioner_id,
            department_id=department_id,
            start_at=start_at,
            end_at=end_at,
        )
        if not fits:
            raise AppointmentOutsideAvailabilityError(
                "The requested time is not within this practitioner's availability."
            )

    async def book_appointment(
        self,
        *,
        organization_id: uuid.UUID,
        patient_id: uuid.UUID,
        practitioner_id: uuid.UUID,
        department_id: uuid.UUID,
        start_at: datetime,
        duration_minutes: int,
        initiated_by_user_id: uuid.UUID | None = None,
    ) -> Appointment:
        """Book a new appointment, committing only once every check passes
        AND the database has confirmed no collision. See the module
        docstring for why there is no pre-check select before the insert.

        `initiated_by_user_id`: see the class docstring "Automatic
        Reminder Scheduling" — when given, a `Reminder` is scheduled for
        this appointment immediately after it is durably booked.
        """
        _validate_duration(duration_minutes)
        end_at = start_at + timedelta(minutes=duration_minutes)
        await self._ensure_bookable(
            organization_id=organization_id,
            patient_id=patient_id,
            practitioner_id=practitioner_id,
            department_id=department_id,
            start_at=start_at,
            end_at=end_at,
        )

        appointment = Appointment(
            organization_id=organization_id,
            patient_id=patient_id,
            practitioner_id=practitioner_id,
            department_id=department_id,
            start_at=start_at,
            end_at=end_at,
            status=AppointmentStatus.BOOKED,
        )
        try:
            await appointment_repository.create(self._session, appointment)
        except IntegrityError as exc:
            await self._session.rollback()
            conflict = _translate_integrity_error(exc)
            if conflict is None:
                raise
            raise conflict from exc

        await self._session.commit()

        if initiated_by_user_id is not None:
            await self._schedule_reminder_best_effort(
                appointment, initiated_by_user_id=initiated_by_user_id
            )
        return appointment

    async def get_appointment(
        self,
        *,
        organization_id: uuid.UUID,
        appointment_id: uuid.UUID,
        patient_id: uuid.UUID | None = None,
    ) -> Appointment:
        """Tenant-scoped retrieval by id.

        `patient_id`, when given, additionally restricts the result to an
        appointment belonging to THAT patient — used for `PATIENT`-role
        self-service callers, whose `patient_id` is always derived
        server-side from their own linked `Patient` record (see
        `app.api.v1.endpoints.appointments`), never trusted from a
        request. Raises the same `AppointmentNotFoundError` whether the
        appointment doesn't exist, belongs to another organization, or
        belongs to another patient — no case is distinguishable from the
        response.
        """
        appointment = await appointment_repository.get_by_id(
            self._session, organization_id=organization_id, appointment_id=appointment_id
        )
        if appointment is None:
            raise AppointmentNotFoundError(
                "No appointment found with this id in this organization."
            )
        if patient_id is not None and appointment.patient_id != patient_id:
            raise AppointmentNotFoundError(
                "No appointment found with this id in this organization."
            )
        return appointment

    async def list_appointments(
        self, *, organization_id: uuid.UUID, limit: int = 50, offset: int = 0
    ) -> list[Appointment]:
        """Organization-wide administrative listing — ADMIN/STAFF only
        (enforced by the route, not here; see docs/APPOINTMENTS.md
        "Listing Privacy"). `PATIENT` callers must use
        `list_appointments_for_patient` instead."""
        return list(
            await appointment_repository.list_by_organization(
                self._session, organization_id=organization_id, limit=limit, offset=offset
            )
        )

    async def list_appointments_for_patient(
        self,
        *,
        organization_id: uuid.UUID,
        patient_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Appointment]:
        """Self-scoped listing for exactly one patient."""
        return list(
            await appointment_repository.list_by_patient(
                self._session,
                organization_id=organization_id,
                patient_id=patient_id,
                limit=limit,
                offset=offset,
            )
        )

    async def reschedule_appointment(
        self,
        *,
        organization_id: uuid.UUID,
        appointment_id: uuid.UUID,
        start_at: datetime,
        duration_minutes: int,
        patient_id: uuid.UUID | None = None,
        initiated_by_user_id: uuid.UUID | None = None,
    ) -> Appointment:
        """Move an existing `booked` appointment to a new time, in place —
        deliberately NOT cancel-old-plus-create-new (see
        docs/adr/ADR-0007-appointment-concurrency.md): the appointment
        keeps its identity (`id`) and full history across a reschedule.

        Re-runs the FULL booking validation (`_ensure_bookable`) against
        the appointment's existing practitioner/department/patient — an
        appointment booked when a practitioner/department/assignment was
        active could still be rescheduled after any of those became
        inactive in the meantime, and that must be caught here too, not
        just at original booking time.

        If the update collides with another appointment (the EXCLUDE
        constraint), the transaction is rolled back and
        `AppointmentConflictError` is raised — the ORIGINAL appointment
        row is left completely unchanged in the database, because the
        failed `UPDATE` was never committed.

        `initiated_by_user_id`: see the class docstring "Automatic
        Reminder Scheduling" — when given, every still-cancellable
        reminder for the OLD time is cancelled, and a fresh one is
        scheduled for the NEW time (never left pointing at a stale
        `start_at`).
        """
        appointment = await self.get_appointment(
            organization_id=organization_id,
            appointment_id=appointment_id,
            patient_id=patient_id,
        )
        if appointment.status is not AppointmentStatus.BOOKED:
            raise InvalidAppointmentTransitionError(
                "Only a booked appointment can be rescheduled."
            )

        _validate_duration(duration_minutes)
        end_at = start_at + timedelta(minutes=duration_minutes)
        await self._ensure_bookable(
            organization_id=organization_id,
            patient_id=appointment.patient_id,
            practitioner_id=appointment.practitioner_id,
            department_id=appointment.department_id,
            start_at=start_at,
            end_at=end_at,
        )

        appointment.start_at = start_at
        appointment.end_at = end_at
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            conflict = _translate_integrity_error(exc)
            if conflict is None:
                raise
            raise conflict from exc

        await self._session.commit()

        if initiated_by_user_id is not None:
            await self._cancel_reminders_best_effort(
                appointment, initiated_by_user_id=initiated_by_user_id
            )
            await self._schedule_reminder_best_effort(
                appointment, initiated_by_user_id=initiated_by_user_id
            )
        return appointment

    async def cancel_appointment(
        self,
        *,
        organization_id: uuid.UUID,
        appointment_id: uuid.UUID,
        cancellation_reason: str | None = None,
        patient_id: uuid.UUID | None = None,
        initiated_by_user_id: uuid.UUID | None = None,
    ) -> Appointment:
        """`booked` -> `cancelled`.

        Rejected (not silently ignored) if the appointment is already
        `cancelled` or is `completed` — repeated/duplicate cancellation
        attempts get the same `InvalidAppointmentTransitionError` as any
        other invalid transition, a single consistent rule rather than a
        special-cased idempotent no-op. See docs/APPOINTMENTS.md
        "Cancellation".

        Never a collision: moving a row's status AWAY FROM `booked` can
        never violate either EXCLUDE constraint (both apply only `WHERE
        status = 'booked'`), so no `IntegrityError` handling is needed
        here.

        `initiated_by_user_id`: see the class docstring "Automatic
        Reminder Scheduling" — when given, every still-cancellable
        reminder for this appointment is cancelled too.
        """
        appointment = await self.get_appointment(
            organization_id=organization_id,
            appointment_id=appointment_id,
            patient_id=patient_id,
        )
        if appointment.status is not AppointmentStatus.BOOKED:
            raise InvalidAppointmentTransitionError("Only a booked appointment can be cancelled.")

        appointment.status = AppointmentStatus.CANCELLED
        appointment.cancellation_reason = cancellation_reason
        await self._session.flush()
        await self._session.commit()

        if initiated_by_user_id is not None:
            await self._cancel_reminders_best_effort(
                appointment, initiated_by_user_id=initiated_by_user_id
            )
        return appointment
