"""Mandatory real-concurrency integration test for appointment booking.

STORY-007's central safety requirement: double-booking must be prevented
at the DATABASE level, genuinely race-safe under concurrent transactions
— not merely by a sequential unit test, and not by mocking the database
constraint. This file is the proof.

Unlike every other test in this codebase (which uses the single shared,
savepoint-isolated `db_session` fixture from `tests/conftest.py`), this
test deliberately opens TWO INDEPENDENT database connections/transactions
via its own dedicated engine, and races two real, concurrently-executing
`AppointmentService.book_appointment()` calls against each other with
`asyncio.gather`. Exactly one must succeed; the other must fail with the
exact, expected `AppointmentConflictError` — proven against a real
PostgreSQL instance, with the actual `EXCLUDE` constraints
(`app/models/appointment.py`) as the only thing arbitrating the race.

Setup data is genuinely COMMITTED (not flushed-then-rolled-back like
every other test) — savepoint isolation implies a single underlying
connection, which would defeat the entire point of testing two
independent connections racing each other. Synthetic rows are explicitly
deleted in a `finally` block instead (see `_teardown`).

To run:
    export AGENTCARE_TEST_POSTGRES_URL=postgresql+asyncpg://user:pw@localhost:5432/db
    pytest tests/db/test_appointment_concurrency.py
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.appointment import Appointment
from app.models.department import Department
from app.models.facility import Facility, FacilityType
from app.models.organization import Organization, OrganizationType
from app.models.patient import Patient
from app.models.practitioner import Practitioner, PractitionerType
from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability
from app.models.practitioner_department import PractitionerDepartment
from app.services.appointment import AppointmentConflictError, AppointmentService

_POSTGRES_TEST_URL = os.environ.get("AGENTCARE_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not _POSTGRES_TEST_URL,
    reason="Set AGENTCARE_TEST_POSTGRES_URL to a real PostgreSQL instance to run this test.",
)


@dataclass(frozen=True)
class _Scenario:
    organization_id: uuid.UUID
    facility_id: uuid.UUID
    department_id: uuid.UUID
    practitioner_id: uuid.UUID
    patient_a_id: uuid.UUID
    patient_b_id: uuid.UUID
    second_practitioner_id: uuid.UUID


async def _setup(
    session_factory: async_sessionmaker[AsyncSession], suffix: str
) -> _Scenario:
    """Create and COMMIT a genuinely persisted scenario: one org, one
    facility, one department, TWO practitioners (both assigned to the
    department, both with a Monday-covering, wide-open availability
    window so any `start_at` this test picks always validates), and two
    patients."""
    async with session_factory() as session:
        org = Organization(
            name=f"Synthetic Concurrency Org {suffix}",
            slug=f"synthetic-concurrency-org-{suffix}",
            organization_type=OrganizationType.HOSPITAL,
        )
        session.add(org)
        await session.flush()

        facility = Facility(
            organization_id=org.id,
            name=f"Synthetic Concurrency Facility {suffix}",
            code=f"CONC-{suffix}",
            facility_type=FacilityType.HOSPITAL,
            timezone="UTC",
        )
        session.add(facility)
        await session.flush()

        department = Department(
            organization_id=org.id,
            facility_id=facility.id,
            name=f"Synthetic Concurrency Department {suffix}",
            code=f"CONC-DPT-{suffix}",
        )
        session.add(department)
        await session.flush()

        practitioner = Practitioner(
            organization_id=org.id,
            first_name="Synthetic",
            last_name=f"Concurrency-Prac-{suffix}",
            practitioner_type=PractitionerType.PHYSICIAN,
        )
        second_practitioner = Practitioner(
            organization_id=org.id,
            first_name="Synthetic",
            last_name=f"Concurrency-Prac2-{suffix}",
            practitioner_type=PractitionerType.PHYSICIAN,
        )
        session.add_all([practitioner, second_practitioner])
        await session.flush()

        assignment = PractitionerDepartment(
            organization_id=org.id, practitioner_id=practitioner.id, department_id=department.id
        )
        second_assignment = PractitionerDepartment(
            organization_id=org.id,
            practitioner_id=second_practitioner.id,
            department_id=department.id,
        )
        session.add_all([assignment, second_assignment])
        await session.flush()

        # A wide-open recurring window covering every day of the week, for
        # BOTH practitioners, so this test never needs to compute (or
        # depend on) which weekday a future date happens to fall on.
        for day in DayOfWeek:
            session.add_all(
                [
                    PractitionerAvailability(
                        organization_id=org.id,
                        practitioner_id=practitioner.id,
                        department_id=department.id,
                        day_of_week=day,
                        start_time=datetime.min.time(),
                        end_time=datetime.max.time().replace(microsecond=0),
                        timezone="UTC",
                    ),
                    PractitionerAvailability(
                        organization_id=org.id,
                        practitioner_id=second_practitioner.id,
                        department_id=department.id,
                        day_of_week=day,
                        start_time=datetime.min.time(),
                        end_time=datetime.max.time().replace(microsecond=0),
                        timezone="UTC",
                    ),
                ]
            )

        patient_a = Patient(
            organization_id=org.id,
            patient_number=f"CONC-PN-A-{suffix}",
            first_name="Synthetic",
            last_name="ConcurrencyPatientA",
            date_of_birth=datetime(1990, 1, 1).date(),
        )
        patient_b = Patient(
            organization_id=org.id,
            patient_number=f"CONC-PN-B-{suffix}",
            first_name="Synthetic",
            last_name="ConcurrencyPatientB",
            date_of_birth=datetime(1990, 1, 1).date(),
        )
        session.add_all([patient_a, patient_b])
        await session.flush()

        await session.commit()

        return _Scenario(
            organization_id=org.id,
            facility_id=facility.id,
            department_id=department.id,
            practitioner_id=practitioner.id,
            second_practitioner_id=second_practitioner.id,
            patient_a_id=patient_a.id,
            patient_b_id=patient_b.id,
        )


async def _teardown(session_factory: async_sessionmaker[AsyncSession], scenario: _Scenario) -> None:
    """Explicitly delete every synthetic row this test created, in
    FK-safe (child-before-parent) order — required because this test's
    data is genuinely committed, not rolled back (see the module
    docstring)."""
    async with session_factory() as session:
        await session.execute(
            delete(Appointment).where(Appointment.organization_id == scenario.organization_id)
        )
        await session.execute(
            delete(PractitionerAvailability).where(
                PractitionerAvailability.organization_id == scenario.organization_id
            )
        )
        await session.execute(
            delete(PractitionerDepartment).where(
                PractitionerDepartment.organization_id == scenario.organization_id
            )
        )
        await session.execute(
            delete(Patient).where(Patient.organization_id == scenario.organization_id)
        )
        await session.execute(
            delete(Practitioner).where(Practitioner.organization_id == scenario.organization_id)
        )
        await session.execute(
            delete(Department).where(Department.organization_id == scenario.organization_id)
        )
        await session.execute(
            delete(Facility).where(Facility.organization_id == scenario.organization_id)
        )
        await session.execute(
            delete(Organization).where(Organization.id == scenario.organization_id)
        )
        await session.commit()


def _next_monday_9am_utc() -> datetime:
    now = datetime.now(UTC)
    days_ahead = (7 - now.weekday()) % 7 or 7
    target = (now + timedelta(days=days_ahead)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    return target


async def test_concurrent_overlapping_practitioner_booking_only_one_succeeds() -> None:
    """Two independent connections race to book the SAME practitioner over
    an OVERLAPPING time, for two DIFFERENT patients. Exactly one must
    succeed; the other must fail with `AppointmentConflictError` — proven
    via real, concurrently-executing transactions, not sequential calls."""
    assert _POSTGRES_TEST_URL is not None  # narrows type; guarded by skipif above
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    start_at = _next_monday_9am_utc()

    async def _book(patient_id: uuid.UUID, start_offset_minutes: int) -> object:
        async with session_factory() as session:
            service = AppointmentService(session)
            try:
                return await service.book_appointment(
                    organization_id=scenario.organization_id,
                    patient_id=patient_id,
                    practitioner_id=scenario.practitioner_id,
                    department_id=scenario.department_id,
                    start_at=start_at + timedelta(minutes=start_offset_minutes),
                    duration_minutes=30,
                )
            except AppointmentConflictError as exc:
                return exc

    try:
        # 09:00-09:30 and 09:15-09:45 overlap (see docs/APPOINTMENTS.md
        # "Collision Prevention" for this exact example).
        result_a, result_b = await asyncio.gather(
            _book(scenario.patient_a_id, 0),
            _book(scenario.patient_b_id, 15),
        )

        results = [result_a, result_b]
        successes = [r for r in results if isinstance(r, AppointmentConflictError) is False]
        conflicts = [r for r in results if isinstance(r, AppointmentConflictError)]

        assert len(successes) == 1, (
            f"expected exactly one booking to succeed under real concurrency, got: {results}"
        )
        assert len(conflicts) == 1, (
            f"expected exactly one AppointmentConflictError under real concurrency, got: {results}"
        )

        # Confirm the database itself only ever holds ONE row for this
        # practitioner over this window — not an application-level count.
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(Appointment).where(
                            Appointment.organization_id == scenario.organization_id,
                            Appointment.practitioner_id == scenario.practitioner_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()


async def test_concurrent_overlapping_patient_booking_only_one_succeeds() -> None:
    """Two independent connections race to book the SAME patient against
    TWO DIFFERENT practitioners over an overlapping time. Exactly one must
    succeed; the other must fail with `AppointmentConflictError` — this is
    the patient-side EXCLUDE constraint
    (`ex_appointments_patient_no_overlap`), proven under the same
    real-concurrency conditions as the practitioner-side test above. See
    docs/APPOINTMENTS.md "Patient Double-Booking" for the policy this
    proves is actually enforced, not merely documented."""
    assert _POSTGRES_TEST_URL is not None  # narrows type; guarded by skipif above
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    start_at = _next_monday_9am_utc()

    async def _book(practitioner_id: uuid.UUID, start_offset_minutes: int) -> object:
        async with session_factory() as session:
            service = AppointmentService(session)
            try:
                return await service.book_appointment(
                    organization_id=scenario.organization_id,
                    patient_id=scenario.patient_a_id,
                    practitioner_id=practitioner_id,
                    department_id=scenario.department_id,
                    start_at=start_at + timedelta(minutes=start_offset_minutes),
                    duration_minutes=30,
                )
            except AppointmentConflictError as exc:
                return exc

    try:
        result_a, result_b = await asyncio.gather(
            _book(scenario.practitioner_id, 0),
            _book(scenario.second_practitioner_id, 15),
        )

        results = [result_a, result_b]
        successes = [r for r in results if isinstance(r, AppointmentConflictError) is False]
        conflicts = [r for r in results if isinstance(r, AppointmentConflictError)]

        assert len(successes) == 1, (
            f"expected exactly one booking to succeed under real concurrency, got: {results}"
        )
        assert len(conflicts) == 1, (
            f"expected exactly one AppointmentConflictError under real concurrency, got: {results}"
        )
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()
