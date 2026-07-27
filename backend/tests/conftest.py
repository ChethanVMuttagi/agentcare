"""Root test fixtures, shared across every test package.

`db_session` and the `make_*` factory fixtures require
`AGENTCARE_TEST_POSTGRES_URL` (a real, reachable PostgreSQL instance with
migrations applied) and are skipped otherwise — see the `db_session`
fixture. Each test runs inside an outer connection-level transaction that
is always rolled back afterward (SQLAlchemy's
`join_transaction_mode="create_savepoint"` pattern), so no synthetic test
data is ever actually persisted. Only obviously-synthetic data
(`@example.com` emails, `synthetic-*` slugs) is ever used.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date, time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.security import hash_password
from app.db.session import get_db_session
from app.main import create_app
from app.models.department import Department
from app.models.facility import Facility, FacilityType
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization, OrganizationType
from app.models.patient import Patient
from app.models.practitioner import Practitioner, PractitionerType
from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability
from app.models.practitioner_department import PractitionerDepartment
from app.models.user import User


@pytest.fixture()
def app() -> FastAPI:
    return create_app()


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


_POSTGRES_TEST_URL = os.environ.get("AGENTCARE_TEST_POSTGRES_URL")


@pytest.fixture()
async def db_session() -> AsyncIterator[AsyncSession]:
    if not _POSTGRES_TEST_URL:
        pytest.skip(
            "Set AGENTCARE_TEST_POSTGRES_URL to a real PostgreSQL instance "
            "(with `alembic upgrade head` applied) to run tests that need a database."
        )

    engine = create_async_engine(_POSTGRES_TEST_URL)
    async with engine.connect() as connection:
        await connection.begin()
        session_factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with session_factory() as session:
            yield session
        await connection.rollback()
    await engine.dispose()


@pytest.fixture()
async def client_with_db(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An async client whose `get_db_session` dependency is overridden to
    use the same rolled-back-afterward `db_session`, so data created via
    `make_user`/`make_organization`/etc. in a test is visible to the
    request the client makes, and nothing persists afterward.

    Deliberately `httpx.AsyncClient` + `ASGITransport`, NOT
    `starlette.testclient.TestClient`: `TestClient` dispatches requests
    through its own background thread/event loop (an anyio "portal"),
    which breaks `db_session`'s asyncpg connection — asyncpg connections
    are bound to the event loop that created them and cannot be used from
    a different one ("Future attached to a different loop"). `AsyncClient`
    with `ASGITransport` runs the request in the *same* event loop as the
    calling test, so the one `db_session` connection is used consistently
    end-to-end.
    """

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture()
def make_organization(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[Organization]]:
    """Factory for a synthetic, flushed (never committed) Organization."""

    async def _make(
        slug_suffix: str,
        organization_type: OrganizationType = OrganizationType.HOSPITAL,
    ) -> Organization:
        org = Organization(
            name=f"Synthetic Test Organization {slug_suffix}",
            slug=f"synthetic-test-org-{slug_suffix}",
            organization_type=organization_type,
        )
        db_session.add(org)
        await db_session.flush()
        return org

    return _make


@pytest.fixture()
def make_user(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[User]]:
    """Factory for a synthetic, flushed (never committed) User.

    Uses the `example.com` domain (RFC 2606: reserved specifically for
    documentation/examples, guaranteed never to be a real, registered
    mail-receiving domain) so these addresses can never collide with a
    real one. `.invalid` (also RFC 2606) would be even more obviously
    fake, but pydantic's `EmailStr` (via `email-validator`) rejects it
    outright as a "special-use or reserved name" — `example.com` is the
    practical choice that both libraries accept. Uses a default synthetic
    password distinct from any real credential.
    """

    async def _make(
        email_suffix: str,
        password: str = "Synthetic-Test-Password-123!",
        is_active: bool = True,
    ) -> User:
        user = User(
            email=f"synthetic.test.user.{email_suffix}@example.com",
            password_hash=hash_password(password),
            is_active=is_active,
        )
        db_session.add(user)
        await db_session.flush()
        return user

    return _make


@pytest.fixture()
def make_membership(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[OrganizationMembership]]:
    """Factory for a synthetic, flushed (never committed) OrganizationMembership."""

    async def _make(
        organization: Organization,
        user: User,
        role: Role = Role.STAFF,
        is_active: bool = True,
    ) -> OrganizationMembership:
        membership = OrganizationMembership(
            organization_id=organization.id,
            user_id=user.id,
            role=role,
            is_active=is_active,
        )
        db_session.add(membership)
        await db_session.flush()
        return membership

    return _make


@pytest.fixture()
def make_patient(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[Patient]]:
    """Factory for a synthetic, flushed (never committed) Patient.

    `patient_number` has no default — callers should pass an obviously
    synthetic, test-unique value (uniqueness is only enforced per
    organization, so plain suffixed values like ``"PN-001"`` are fine
    within a single test unless testing the conflict itself). `user`, if
    given, links the patient to that `User`'s `id` — this fixture does
    NOT validate organization-membership/role linkage rules itself (that
    is `app.services.patient`'s job); it is a raw factory for model-level
    and repository-level tests that need to set up rows directly.
    """

    async def _make(
        organization: Organization,
        patient_number: str,
        *,
        user: User | None = None,
        first_name: str = "Synthetic",
        last_name: str = "Patient",
        date_of_birth: date = date(1990, 1, 1),
        is_active: bool = True,
    ) -> Patient:
        patient = Patient(
            organization_id=organization.id,
            user_id=user.id if user is not None else None,
            patient_number=patient_number,
            first_name=first_name,
            last_name=last_name,
            date_of_birth=date_of_birth,
            is_active=is_active,
        )
        db_session.add(patient)
        await db_session.flush()
        return patient

    return _make


@pytest.fixture()
def make_facility(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[Facility]]:
    """Factory for a synthetic, flushed (never committed) Facility."""

    async def _make(
        organization: Organization,
        code_suffix: str,
        facility_type: FacilityType = FacilityType.HOSPITAL,
        timezone: str = "UTC",
    ) -> Facility:
        facility = Facility(
            organization_id=organization.id,
            name=f"Synthetic Test Facility {code_suffix}",
            code=f"FAC-{code_suffix}",
            facility_type=facility_type,
            timezone=timezone,
        )
        db_session.add(facility)
        await db_session.flush()
        return facility

    return _make


@pytest.fixture()
def make_department(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[Department]]:
    """Factory for a synthetic, flushed (never committed) Department.

    Does NOT validate facility/organization ownership itself (that is
    `app.services.department`'s job) — this is a raw factory for
    model-level and repository-level tests that need to set up rows
    directly, so it can also be used to construct deliberately-invalid
    rows (e.g. a mismatched `organization`/`facility` pair) for
    constraint tests.
    """

    async def _make(
        organization: Organization,
        facility: Facility,
        code: str,
        *,
        name: str | None = None,
        is_active: bool = True,
    ) -> Department:
        department = Department(
            organization_id=organization.id,
            facility_id=facility.id,
            name=name or f"Synthetic Test Department {code}",
            code=code,
            is_active=is_active,
        )
        db_session.add(department)
        await db_session.flush()
        return department

    return _make


@pytest.fixture()
def make_practitioner(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[Practitioner]]:
    """Factory for a synthetic, flushed (never committed) Practitioner."""

    async def _make(
        organization: Organization,
        *,
        first_name: str = "Synthetic",
        last_name: str = "Practitioner",
        practitioner_type: PractitionerType = PractitionerType.PHYSICIAN,
        is_active: bool = True,
    ) -> Practitioner:
        practitioner = Practitioner(
            organization_id=organization.id,
            first_name=first_name,
            last_name=last_name,
            practitioner_type=practitioner_type,
            is_active=is_active,
        )
        db_session.add(practitioner)
        await db_session.flush()
        return practitioner

    return _make


@pytest.fixture()
def make_practitioner_department(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[PractitionerDepartment]]:
    """Factory for a synthetic, flushed (never committed)
    `PractitionerDepartment` assignment. Does NOT validate tenant
    ownership itself — see `app.services.practitioner`."""

    async def _make(
        organization: Organization,
        practitioner: Practitioner,
        department: Department,
        *,
        is_active: bool = True,
    ) -> PractitionerDepartment:
        assignment = PractitionerDepartment(
            organization_id=organization.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            is_active=is_active,
        )
        db_session.add(assignment)
        await db_session.flush()
        return assignment

    return _make


@pytest.fixture()
def make_availability(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[PractitionerAvailability]]:
    """Factory for a synthetic, flushed (never committed)
    `PractitionerAvailability` window. Does NOT validate assignment,
    time-range, timezone, or overlap rules itself — see
    `app.services.availability`."""

    async def _make(
        organization: Organization,
        practitioner: Practitioner,
        department: Department,
        *,
        day_of_week: DayOfWeek = DayOfWeek.MONDAY,
        start_time: time = time(9, 0),
        end_time: time = time(12, 0),
        timezone: str = "UTC",
        is_active: bool = True,
    ) -> PractitionerAvailability:
        availability = PractitionerAvailability(
            organization_id=organization.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            day_of_week=day_of_week,
            start_time=start_time,
            end_time=end_time,
            timezone=timezone,
            is_active=is_active,
        )
        db_session.add(availability)
        await db_session.flush()
        return availability

    return _make
