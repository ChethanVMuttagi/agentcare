"""Lightweight load/performance tests (Sprint 3) for core read paths.

Not a dedicated load-testing tool (no Locust/k6/etc — this codebase
doesn't use one anywhere): `httpx.AsyncClient` + `asyncio.gather` firing
many concurrent requests at the real ASGI app through a real,
pool-backed engine (mirroring `tests/db/test_appointment_concurrency.py`'s
"dedicated real concurrency" pattern — genuinely committed setup data,
not the savepoint-rolled-back `db_session` fixture, since that fixture
serializes on one connection and can't serve true concurrent access —
see `tests/api/test_rate_limiting_concurrency.py` for the same
reasoning), asserting every request succeeds and mean latency stays
within a generous bound. This measures concurrent-request handling, not
sustained throughput over wall-clock time — it runs in seconds, as part
of the normal `pytest` invocation.

To run:
    export AGENTCARE_TEST_POSTGRES_URL=postgresql+asyncpg://user:pw@localhost:5432/db
    pytest tests/performance/test_core_workflow_load.py
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from datetime import time as dtime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.jwt import create_access_token
from app.auth.security import hash_password
from app.core.config import get_settings
from app.db.session import dispose_engine, get_engine, get_sessionmaker
from app.main import create_app
from app.models.department import Department
from app.models.facility import Facility, FacilityType
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization, OrganizationType
from app.models.practitioner import Practitioner, PractitionerType
from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability
from app.models.practitioner_department import PractitionerDepartment
from app.models.user import User

_POSTGRES_TEST_URL = os.environ.get("AGENTCARE_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not _POSTGRES_TEST_URL,
    reason="Set AGENTCARE_TEST_POSTGRES_URL to a real PostgreSQL instance to run this test.",
)

_CONCURRENCY = 60
# Generous on purpose: this asserts "the app doesn't fall over/hang under
# concurrent load," not a tuned production SLA — CI runners are slow and
# variable, and a flaky perf test is worse than a loose one.
_MAX_MEAN_LATENCY_SECONDS = 3.0
_SYNTHETIC_JWT_SECRET = "synthetic-test-jwt-secret-do-not-use-in-production-32chars"
_PASSWORD = "Synthetic-Test-Password-123!"


@dataclass(frozen=True)
class _Scenario:
    organization_id: uuid.UUID
    department_id: uuid.UUID
    practitioner_id: uuid.UUID
    user_id: uuid.UUID


@pytest.fixture(autouse=True)
def _configure_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET_KEY", _SYNTHETIC_JWT_SECRET)
    monkeypatch.setenv("DATABASE_URL", _POSTGRES_TEST_URL or "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _setup(session_factory: async_sessionmaker[AsyncSession], suffix: str) -> _Scenario:
    """Genuinely COMMITTED setup data (not flushed-then-rolled-back) —
    required so the many independent pooled connections the concurrent
    requests below open can all see it. See the module docstring."""
    async with session_factory() as session:
        org = Organization(
            name=f"Synthetic Load Org {suffix}",
            slug=f"synthetic-load-org-{suffix}",
            organization_type=OrganizationType.HOSPITAL,
        )
        session.add(org)
        await session.flush()

        facility = Facility(
            organization_id=org.id,
            name=f"Synthetic Load Facility {suffix}",
            code=f"LOAD-{suffix}",
            facility_type=FacilityType.HOSPITAL,
            timezone="UTC",
        )
        session.add(facility)
        await session.flush()

        department = Department(
            organization_id=org.id,
            facility_id=facility.id,
            name=f"Synthetic Load Department {suffix}",
            code=f"LOAD-DPT-{suffix}",
        )
        session.add(department)
        await session.flush()

        practitioner = Practitioner(
            organization_id=org.id,
            first_name="Synthetic",
            last_name=f"Load-Prac-{suffix}",
            practitioner_type=PractitionerType.PHYSICIAN,
        )
        session.add(practitioner)
        await session.flush()

        assignment = PractitionerDepartment(
            organization_id=org.id, practitioner_id=practitioner.id, department_id=department.id
        )
        session.add(assignment)
        await session.flush()

        for day in DayOfWeek:
            session.add(
                PractitionerAvailability(
                    organization_id=org.id,
                    practitioner_id=practitioner.id,
                    department_id=department.id,
                    day_of_week=day,
                    start_time=dtime(0, 0),
                    end_time=dtime(23, 59, 59),
                    timezone="UTC",
                )
            )

        user = User(
            email=f"synthetic.load.{suffix}@example.com",
            password_hash=hash_password(_PASSWORD),
        )
        session.add(user)
        await session.flush()

        membership = OrganizationMembership(
            organization_id=org.id, user_id=user.id, role=Role.ADMIN
        )
        session.add(membership)

        await session.commit()

        return _Scenario(
            organization_id=org.id,
            department_id=department.id,
            practitioner_id=practitioner.id,
            user_id=user.id,
        )


async def _teardown(session_factory: async_sessionmaker[AsyncSession], scenario: _Scenario) -> None:
    """FK-safe (child-before-parent) cleanup — required because this
    test's data is genuinely committed, not rolled back."""
    async with session_factory() as session:
        await session.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id == scenario.organization_id
            )
        )
        await session.execute(delete(User).where(User.id == scenario.user_id))
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


@pytest.fixture()
async def _load_scenario() -> AsyncIterator[tuple[AsyncClient, _Scenario, str]]:
    assert _POSTGRES_TEST_URL is not None  # narrows type; guarded by skipif above
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=20, max_overflow=20)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    app: FastAPI = create_app()
    token = create_access_token(scenario.user_id, get_settings())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, scenario, token

    await dispose_engine()
    await _teardown(session_factory, scenario)
    await engine.dispose()


async def test_concurrent_workflow_list_requests_succeed_within_latency_bound(
    _load_scenario: tuple[AsyncClient, _Scenario, str],
) -> None:
    client, scenario, token = _load_scenario
    headers = {"Authorization": f"Bearer {token}"}
    url = f"/api/v1/organizations/{scenario.organization_id}/workflows"

    start = time.monotonic()
    responses = await asyncio.gather(
        *[client.get(url, headers=headers) for _ in range(_CONCURRENCY)]
    )
    elapsed = time.monotonic() - start

    assert all(response.status_code == 200 for response in responses)
    assert (elapsed / _CONCURRENCY) < _MAX_MEAN_LATENCY_SECONDS


async def test_concurrent_available_times_requests_succeed_within_latency_bound(
    _load_scenario: tuple[AsyncClient, _Scenario, str],
) -> None:
    client, scenario, token = _load_scenario
    headers = {"Authorization": f"Bearer {token}"}
    appointment_date = date.today() + timedelta(days=14)
    url = (
        f"/api/v1/organizations/{scenario.organization_id}/practitioners/"
        f"{scenario.practitioner_id}/available-times"
    )

    start = time.monotonic()
    responses = await asyncio.gather(
        *[
            client.get(
                url,
                params={
                    "department_id": str(scenario.department_id),
                    "date": appointment_date.isoformat(),
                    "duration_minutes": 30,
                },
                headers=headers,
            )
            for _ in range(_CONCURRENCY)
        ]
    )
    elapsed = time.monotonic() - start

    assert all(response.status_code == 200 for response in responses)
    assert (elapsed / _CONCURRENCY) < _MAX_MEAN_LATENCY_SECONDS
