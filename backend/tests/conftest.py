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
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.security import hash_password
from app.core.config import Settings
from app.core.rate_limit import limiter
from app.db.session import get_db_session
from app.main import create_app
from app.models.appointment import Appointment, AppointmentStatus
from app.models.approval import ApprovalRequest, ApprovalStatus, ApprovalType
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
from app.models.reminder import (
    Reminder,
    ReminderAttempt,
    ReminderAttemptStatus,
    ReminderStatus,
    ReminderType,
)
from app.models.user import User
from app.models.workflow import (
    ActorType,
    StepStatus,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowRequestType,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)
from app.storage.factory import get_document_storage
from app.storage.local import LocalDocumentStorage


@pytest.fixture(autouse=True)
def _ignore_dotenv_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `Settings()` ignore `backend/.env` for the whole test suite.

    CI checks out a tree with no `.env` at all, so CI is the definition of
    correct here — but a developer running the same tests locally almost
    always DOES have one (a real `DATABASE_URL`, `LLM_PROVIDER=groq`, and
    so on). Several tests assert unconfigured-by-default behavior, and
    `monkeypatch.delenv` alone can't express that: it removes the process
    environment variable, while `Settings`' own `env_file=".env"` source
    keeps supplying the value from disk. Those tests would then fail
    locally for a reason that has nothing to do with the code under test,
    and the workaround — temporarily moving `.env` aside around a test
    run — risks leaving a developer's real configuration displaced if the
    run dies partway.

    Patching `model_config` (a plain dict, restored automatically by
    monkeypatch) makes local runs behave exactly like CI. Tests that need
    configuration still set it explicitly, via `monkeypatch.setenv` or
    `Settings(...)` keyword arguments — both of which take priority over
    the env file anyway, so nothing that legitimately passes in CI is
    affected.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Every test starts with a clean rate-limit bucket state.

    `limiter` (`app.core.rate_limit`) is a module-level singleton —
    `@limiter.limit(...)` decorators are applied once, when
    `app.api.v1.endpoints.auth`/`agent` are first imported, so its
    storage is independent of the `app` fixture rebuilding a fresh
    `FastAPI` instance per test. Without this reset, a test late in the
    suite could spuriously receive 429 from rate-limit state accumulated
    by unrelated earlier tests hitting the same endpoint (see
    `tests/api/test_rate_limiting.py` for the tests that deliberately
    DO exceed a limit, within a single test, against this clean slate).
    """
    limiter.reset()


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
def local_storage(tmp_path: Path) -> LocalDocumentStorage:
    """A `LocalDocumentStorage` rooted at a pytest-managed temporary
    directory — isolated per test, never a tracked source path, and
    cleaned up automatically by pytest afterward. Never point storage
    tests at `backend/local_storage/` (the real configured dev path)."""
    return LocalDocumentStorage(tmp_path / "documents")


@pytest.fixture()
async def client_with_storage(
    app: FastAPI, db_session: AsyncSession, local_storage: LocalDocumentStorage
) -> AsyncIterator[AsyncClient]:
    """Like `client_with_db`, plus overrides `get_document_storage` to use
    an isolated, temporary-directory-backed `LocalDocumentStorage` —
    never the real configured `DOCUMENT_STORAGE_PATH`."""

    async def _db_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _storage_override() -> LocalDocumentStorage:
        return local_storage

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_document_storage] = _storage_override
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


@pytest.fixture()
def make_appointment(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[Appointment]]:
    """Factory for a synthetic, flushed (never committed) `Appointment`.

    Does NOT validate tenant ownership, availability, or collision rules
    itself (that is `app.services.appointment`'s job) — this is a raw
    factory for model-level and repository-level tests. `start_at`
    defaults to a fixed, always-future UTC instant so tests don't need to
    reason about "now" unless they specifically want to.
    """

    async def _make(
        organization: Organization,
        patient: Patient,
        practitioner: Practitioner,
        department: Department,
        *,
        start_at: datetime | None = None,
        duration_minutes: int = 30,
        status: AppointmentStatus = AppointmentStatus.BOOKED,
        cancellation_reason: str | None = None,
    ) -> Appointment:
        resolved_start = start_at or (datetime.now(UTC) + timedelta(days=30))
        appointment = Appointment(
            organization_id=organization.id,
            patient_id=patient.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            start_at=resolved_start,
            end_at=resolved_start + timedelta(minutes=duration_minutes),
            status=status,
            cancellation_reason=cancellation_reason,
        )
        db_session.add(appointment)
        await db_session.flush()
        return appointment

    return _make


@pytest.fixture()
def make_patient_document(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[PatientDocument]]:
    """Factory for a synthetic, flushed (never committed) `PatientDocument`.

    Does NOT validate uploader membership, file content, or storage
    orchestration itself (that is `app.services.document`'s job) — this
    is a raw factory for model-level and repository-level tests. The
    caller is responsible for `uploaded_by_user_id` referencing a user
    with an ACTUAL `OrganizationMembership` in `organization` (the
    composite FK requires it — see `app/models/patient_document.py`).
    `storage_key` defaults to a unique, opaque-looking value per call so
    tests don't collide on the `UNIQUE(storage_key)` constraint unless
    deliberately testing that.
    """

    async def _make(
        organization: Organization,
        patient: Patient,
        uploaded_by_user_id: uuid.UUID,
        *,
        document_type: DocumentType = DocumentType.OTHER,
        status: DocumentStatus = DocumentStatus.AVAILABLE,
        original_filename: str = "synthetic-document.pdf",
        storage_key: str | None = None,
        media_type: DocumentMediaType = DocumentMediaType.PDF,
        size_bytes: int | None = 1024,
        sha256: str | None = "0" * 64,
    ) -> PatientDocument:
        document = PatientDocument(
            organization_id=organization.id,
            patient_id=patient.id,
            uploaded_by_user_id=uploaded_by_user_id,
            document_type=document_type,
            status=status,
            original_filename=original_filename,
            storage_key=storage_key or f"{organization.id.hex}/{uuid.uuid4().hex}",
            media_type=media_type,
            size_bytes=size_bytes if status == DocumentStatus.AVAILABLE else None,
            sha256=sha256 if status == DocumentStatus.AVAILABLE else None,
        )
        db_session.add(document)
        await db_session.flush()
        return document

    return _make


@pytest.fixture()
def make_workflow_run(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[WorkflowRun]]:
    """Factory for a synthetic, flushed (never committed) `WorkflowRun`.

    Does NOT validate initiator membership, patient ownership, or
    transition rules itself (that is `app.services.workflow`'s job) —
    this is a raw factory for model-level and repository-level tests.
    `correlation_id` defaults to a fresh, unique, opaque value per call
    (the same shape `WorkflowService._new_correlation_id` generates) so
    tests don't collide on `UNIQUE(correlation_id)` unless deliberately
    testing that.
    """

    async def _make(
        organization: Organization,
        initiated_by_user_id: uuid.UUID,
        *,
        patient: Patient | None = None,
        request_type: WorkflowRequestType = WorkflowRequestType.APPOINTMENT_BOOKING,
        status: WorkflowStatus = WorkflowStatus.PENDING,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
        current_step: int | None = None,
        failure_code: str | None = None,
        failure_message_safe: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> WorkflowRun:
        run = WorkflowRun(
            organization_id=organization.id,
            patient_id=patient.id if patient is not None else None,
            initiated_by_user_id=initiated_by_user_id,
            request_type=request_type,
            status=status,
            correlation_id=correlation_id or uuid.uuid4().hex,
            idempotency_key=idempotency_key,
            current_step=current_step,
            failure_code=failure_code,
            failure_message_safe=failure_message_safe,
            started_at=started_at,
            completed_at=completed_at,
        )
        db_session.add(run)
        await db_session.flush()
        return run

    return _make


@pytest.fixture()
def make_workflow_step(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[WorkflowStep]]:
    """Factory for a synthetic, flushed (never committed) `WorkflowStep`.

    Does NOT validate run status/transition rules itself — this is a raw
    factory for model-level and repository-level tests.
    """

    async def _make(
        organization: Organization,
        workflow_run: WorkflowRun,
        sequence_number: int,
        *,
        step_type: str = "synthetic_step",
        status: StepStatus = StepStatus.PENDING,
        agent_name: str | None = None,
        tool_name: str | None = None,
        attempt_count: int = 0,
        failure_code: str | None = None,
        failure_message_safe: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> WorkflowStep:
        step = WorkflowStep(
            organization_id=organization.id,
            workflow_run_id=workflow_run.id,
            sequence_number=sequence_number,
            step_type=step_type,
            status=status,
            agent_name=agent_name,
            tool_name=tool_name,
            attempt_count=attempt_count,
            failure_code=failure_code,
            failure_message_safe=failure_message_safe,
            started_at=started_at,
            completed_at=completed_at,
        )
        db_session.add(step)
        await db_session.flush()
        return step

    return _make


@pytest.fixture()
def make_workflow_event(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[WorkflowEvent]]:
    """Factory for a synthetic, flushed (never committed) `WorkflowEvent`.

    Append-only in production (see `app.repositories.workflow_event`);
    this raw factory exists only for model-level/repository-level test
    setup, not to demonstrate a supported update path.
    """

    async def _make(
        organization: Organization,
        workflow_run: WorkflowRun,
        *,
        workflow_step: WorkflowStep | None = None,
        event_type: WorkflowEventType = WorkflowEventType.WORKFLOW_CREATED,
        actor_type: ActorType = ActorType.SYSTEM,
        actor_identifier: str = "synthetic-actor",
        safe_metadata: dict[str, object] | None = None,
    ) -> WorkflowEvent:
        event = WorkflowEvent(
            organization_id=organization.id,
            workflow_run_id=workflow_run.id,
            workflow_step_id=workflow_step.id if workflow_step is not None else None,
            event_type=event_type,
            actor_type=actor_type,
            actor_identifier=actor_identifier,
            safe_metadata=safe_metadata,
        )
        db_session.add(event)
        await db_session.flush()
        return event

    return _make


@pytest.fixture()
def make_reminder(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[Reminder]]:
    """Factory for a synthetic, flushed (never committed) `Reminder`.

    Does NOT validate lifecycle-transition rules itself (that is
    `app.services.reminder.ReminderService`'s job) — this is a raw
    factory for model-level/repository-level test setup. The caller is
    responsible for `workflow_run`/`workflow_step` referencing a REAL
    run/step in the SAME `organization` (the composite FKs require it —
    see `app/models/reminder.py`).
    """

    async def _make(
        organization: Organization,
        appointment: Appointment,
        patient: Patient,
        workflow_run: WorkflowRun,
        workflow_step: WorkflowStep,
        *,
        reminder_type: ReminderType = ReminderType.APPOINTMENT_REMINDER,
        scheduled_at: datetime | None = None,
        status: ReminderStatus = ReminderStatus.PENDING,
        payload: dict[str, object] | None = None,
        attempt_count: int = 0,
        max_attempts: int = 5,
        locked_at: datetime | None = None,
        locked_by: str | None = None,
        last_error: str | None = None,
        sent_at: datetime | None = None,
        cancelled_at: datetime | None = None,
    ) -> Reminder:
        reminder = Reminder(
            organization_id=organization.id,
            appointment_id=appointment.id,
            patient_id=patient.id,
            workflow_run_id=workflow_run.id,
            workflow_step_id=workflow_step.id,
            reminder_type=reminder_type,
            scheduled_at=scheduled_at or (datetime.now(UTC) + timedelta(days=1)),
            status=status,
            payload=payload,
            attempt_count=attempt_count,
            max_attempts=max_attempts,
            locked_at=locked_at,
            locked_by=locked_by,
            last_error=last_error,
            sent_at=sent_at,
            cancelled_at=cancelled_at,
        )
        db_session.add(reminder)
        await db_session.flush()
        return reminder

    return _make


@pytest.fixture()
def make_reminder_attempt(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[ReminderAttempt]]:
    """Factory for a synthetic, flushed (never committed) `ReminderAttempt`.

    Append-only in production (see `app.repositories.reminder_attempt`);
    this raw factory exists only for model-level/repository-level test
    setup.
    """

    async def _make(
        organization: Organization,
        reminder: Reminder,
        *,
        attempt_number: int = 1,
        status: ReminderAttemptStatus = ReminderAttemptStatus.SENT,
        provider_name: str = "synthetic-provider",
        safe_error_message: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> ReminderAttempt:
        now = datetime.now(UTC)
        attempt = ReminderAttempt(
            organization_id=organization.id,
            reminder_id=reminder.id,
            attempt_number=attempt_number,
            status=status,
            provider_name=provider_name,
            safe_error_message=safe_error_message,
            started_at=started_at or now,
            completed_at=completed_at or now,
        )
        db_session.add(attempt)
        await db_session.flush()
        return attempt

    return _make


@pytest.fixture()
def make_approval_request(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[ApprovalRequest]]:
    """Factory for a synthetic, flushed (never committed) `ApprovalRequest`.

    Does NOT validate lifecycle-transition rules, or pause the
    referenced step/run, itself (that is
    `app.services.approval.ApprovalService`'s job) — this is a raw
    factory for model-level/repository-level test setup. The caller is
    responsible for `workflow_run`/`workflow_step` referencing a REAL
    run/step in the SAME `organization` (the composite FKs require it —
    see `app/models/approval.py`).
    """

    async def _make(
        organization: Organization,
        workflow_run: WorkflowRun,
        workflow_step: WorkflowStep,
        *,
        approval_type: ApprovalType = ApprovalType.HIGH_RISK_ACTION,
        status: ApprovalStatus = ApprovalStatus.PENDING,
        reason: str = "Synthetic test approval reason.",
        requested_by_agent: str = "coordinator",
        approved_by_user: uuid.UUID | None = None,
        approved_at: datetime | None = None,
        rejected_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> ApprovalRequest:
        approval = ApprovalRequest(
            organization_id=organization.id,
            workflow_run_id=workflow_run.id,
            workflow_step_id=workflow_step.id,
            approval_type=approval_type,
            status=status,
            reason=reason,
            requested_by_agent=requested_by_agent,
            approved_by_user=approved_by_user,
            approved_at=approved_at,
            rejected_at=rejected_at,
            expires_at=expires_at or (datetime.now(UTC) + timedelta(hours=24)),
        )
        db_session.add(approval)
        await db_session.flush()
        return approval

    return _make
