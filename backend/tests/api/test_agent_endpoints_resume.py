"""STORY-015 agent endpoint resume tests: `POST .../agent/execute` with
`workflow_run_id` — end-to-end over real HTTP (via a local
`FakeLLMProvider` override), against real PostgreSQL. Mirrors
`tests/api/test_agent_endpoints.py`'s established pattern exactly.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import date

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.coordinator_decisions import (
    CoordinatorClarificationRequiredDecision,
    CoordinatorRefusalDecision,
)
from app.ai.decisions import RefusalCategory
from app.ai.providers.base import LLMProvider
from app.ai.providers.factory import get_llm_provider
from app.ai.providers.fake_provider import FakeLLMProvider
from app.auth.jwt import create_access_token
from app.core.config import get_settings
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.user import User
from app.services.patient import PatientService

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]

_SYNTHETIC_JWT_SECRET = "synthetic-test-jwt-secret-do-not-use-in-production-32chars"


@pytest.fixture(autouse=True)
def _configure_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET_KEY", _SYNTHETIC_JWT_SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token(user.id, get_settings())
    return {"Authorization": f"Bearer {token}"}


def _agent_url(organization: Organization) -> str:
    return f"/api/v1/organizations/{organization.id}/agent/execute"


@asynccontextmanager
async def _client_with_agent(
    app: FastAPI, db_session: AsyncSession, provider: LLMProvider
) -> AsyncIterator[AsyncClient]:
    async def _db_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_llm_provider] = lambda: provider
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)


async def _org_with_member(
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    suffix: str,
    role: Role = Role.ADMIN,
) -> tuple[Organization, User]:
    org = await make_organization(suffix)
    user = await make_user(suffix)
    await make_membership(org, user, role=role)
    return org, user


async def test_follow_up_with_workflow_run_id_resumes_the_paused_run(
    app: FastAPI,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-agent-resume"
    )
    clarifying_provider = FakeLLMProvider(
        coordinator_decision=CoordinatorClarificationRequiredDecision(
            message="Which specialist do you need?"
        )
    )
    async with _client_with_agent(app, db_session, clarifying_provider) as client:
        first_response = await client.post(
            _agent_url(org),
            json={
                "request_type": "administrative_routing",
                "request_text": "Help me with something",
            },
            headers=_auth_header(admin),
        )
    assert first_response.status_code == 201
    first_body = first_response.json()
    assert first_body["workflow_status"] == "waiting"
    assert first_body["decision_kind"] == "clarification_required"

    still_unclear_provider = FakeLLMProvider(
        coordinator_decision=CoordinatorClarificationRequiredDecision(
            message="Still unclear — which department?"
        )
    )
    async with _client_with_agent(app, db_session, still_unclear_provider) as client:
        second_response = await client.post(
            _agent_url(org),
            json={
                "request_type": "administrative_routing",
                "request_text": "I need scheduling help",
                "workflow_run_id": first_body["workflow_id"],
            },
            headers=_auth_header(admin),
        )

    assert second_response.status_code == 201
    second_body = second_response.json()
    assert second_body["workflow_id"] == first_body["workflow_id"]
    assert second_body["workflow_status"] == "waiting"


async def test_resume_unknown_workflow_run_id_returns_not_found(
    app: FastAPI,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-agent-resume-unknown"
    )
    provider = FakeLLMProvider(
        coordinator_decision=CoordinatorClarificationRequiredDecision(message="?")
    )
    async with _client_with_agent(app, db_session, provider) as client:
        response = await client.post(
            _agent_url(org),
            json={
                "request_type": "administrative_routing",
                "request_text": "Anything",
                "workflow_run_id": str(uuid.uuid4()),
            },
            headers=_auth_header(admin),
        )
    assert response.status_code == 404


async def test_resume_already_completed_run_returns_conflict(
    app: FastAPI,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-agent-resume-completed"
    )
    refusal_provider = FakeLLMProvider(
        coordinator_decision=CoordinatorRefusalDecision(
            reason_category=RefusalCategory.OUT_OF_SCOPE, safe_message="No."
        )
    )
    async with _client_with_agent(app, db_session, refusal_provider) as client:
        first_response = await client.post(
            _agent_url(org),
            json={"request_type": "administrative_routing", "request_text": "Hack the mainframe"},
            headers=_auth_header(admin),
        )
    first_body = first_response.json()
    assert first_body["workflow_status"] == "completed"

    resume_provider = FakeLLMProvider(
        coordinator_decision=CoordinatorClarificationRequiredDecision(message="?")
    )
    async with _client_with_agent(app, db_session, resume_provider) as client:
        second_response = await client.post(
            _agent_url(org),
            json={
                "request_type": "administrative_routing",
                "request_text": "Anything",
                "workflow_run_id": first_body["workflow_id"],
            },
            headers=_auth_header(admin),
        )
    assert second_response.status_code == 409


async def test_patient_cannot_resume_another_patients_workflow(
    app: FastAPI,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-agent-resume-cross-patient"
    )
    patient_service = PatientService(db_session)
    owner_patient = await patient_service.create_patient(
        organization_id=org.id,
        patient_number="PN-api-agent-resume-owner",
        first_name="Owner",
        last_name="Patient",
        date_of_birth=date(1990, 1, 1),
    )

    clarifying_provider = FakeLLMProvider(
        coordinator_decision=CoordinatorClarificationRequiredDecision(message="?")
    )
    async with _client_with_agent(app, db_session, clarifying_provider) as client:
        first_response = await client.post(
            _agent_url(org),
            json={
                "request_type": "document_collection",
                "request_text": "Help",
                "patient_id": str(owner_patient.id),
            },
            headers=_auth_header(admin),
        )
    first_body = first_response.json()

    caller_user = await make_user("api-agent-resume-cross-patient-caller")
    await make_membership(org, caller_user, role=Role.PATIENT)
    await patient_service.create_patient(
        organization_id=org.id,
        patient_number="PN-api-agent-resume-caller",
        first_name="Caller",
        last_name="Patient",
        date_of_birth=date(1992, 2, 2),
        user_id=caller_user.id,
    )

    resume_provider = FakeLLMProvider(
        coordinator_decision=CoordinatorClarificationRequiredDecision(message="?")
    )
    async with _client_with_agent(app, db_session, resume_provider) as client:
        second_response = await client.post(
            _agent_url(org),
            json={
                "request_type": "document_collection",
                "request_text": "Trying to resume someone else's run",
                "workflow_run_id": first_body["workflow_id"],
            },
            headers=_auth_header(caller_user),
        )
    assert second_response.status_code == 404
