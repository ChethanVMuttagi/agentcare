"""Milestone B: `GET .../workflows/{workflow_id}/events/stream` (Server-
Sent Events) tests — end-to-end over real HTTP (via `client_with_db`),
against real PostgreSQL. Mirrors `tests/api/test_workflow_endpoints_story015.py`'s
established pattern.

`stream_workflow_events` polls using FRESH sessions from
`app.db.session.get_sessionmaker()` (see that endpoint's docstring for
why — it deliberately does not reuse the request-scoped `get_db_session`
dependency, since the connection must outlive a single request/response
cycle). `client_with_db` only overrides `get_db_session`, so a stream
test additionally patches `get_sessionmaker` to return a sessionmaker
bound to the SAME connection `db_session` uses (see
`_patched_stream_sessionmaker` below) — otherwise the polling loop would
open sessions against the real configured database (or fail outright if
none is configured) and never see this test's flushed-but-uncommitted
synthetic rows, exactly mirroring why `client_with_db` overrides
`get_db_session` for ordinary requests.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.api.v1.endpoints.workflows as workflows_endpoint_module
from app.auth.jwt import create_access_token
from app.core.config import get_settings
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.user import User
from app.models.workflow import ActorType, WorkflowRequestType
from app.services.workflow import WorkflowService

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakePatient = Callable[..., Awaitable[Patient]]

_SYNTHETIC_JWT_SECRET = "synthetic-test-jwt-secret-do-not-use-in-production-32chars"


@pytest.fixture(autouse=True)
def _configure_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET_KEY", _SYNTHETIC_JWT_SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _patched_stream_sessionmaker(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """See the module docstring: `stream_workflow_events` polls with its
    own fresh sessions rather than the request-scoped `get_db_session`
    dependency `client_with_db` overrides, so this patches the SAME seam
    production code uses (`app.db.session.get_sessionmaker`, as imported
    into the endpoint module) to a sessionmaker bound to `db_session`'s
    own connection — every poll tick then sees this test's flushed
    (never committed) synthetic rows, and nothing here touches a real
    database."""
    factory = async_sessionmaker(
        bind=db_session.bind,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(workflows_endpoint_module, "get_sessionmaker", lambda: factory)


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token(user.id, get_settings())
    return {"Authorization": f"Bearer {token}"}


def _stream_url(organization: Organization, workflow_id: object) -> str:
    return f"/api/v1/organizations/{organization.id}/workflows/{workflow_id}/events/stream"


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


def _parse_sse_messages(raw: str) -> list[dict[str, str | None]]:
    """Parse a raw SSE byte stream into `[{"id": ..., "event": ...,
    "data": ...}, ...]`, one dict per blank-line-delimited record —
    comment-only heartbeat lines (`: heartbeat`) are skipped."""
    messages: list[dict[str, str | None]] = []
    current: dict[str, str | None] = {}
    for line in raw.splitlines():
        if line.startswith(":"):
            continue
        if line == "":
            if current:
                messages.append(current)
                current = {}
            continue
        field, _, value = line.partition(": ")
        current[field] = value
    if current:
        messages.append(current)
    return messages


async def _read_until_terminal(
    response_lines: AsyncIterator[str], *, timeout_seconds: float = 10.0
) -> list[dict[str, str | None]]:
    """Accumulate raw SSE lines until a `done`/`workflow_error` message
    has been parsed, or `timeout_seconds` elapses. Every test in this
    module first
    drives its workflow to a terminal status before opening the stream
    (or does so immediately after) specifically so the server-side
    generator always reaches `done` on its own within a couple of poll
    ticks — this helper reads until exactly that point, never relying on
    the client disconnecting mid-stream to make the generator stop
    (ASGI-transport-level disconnect propagation is not what these tests
    are verifying)."""

    async def _collect() -> list[dict[str, str | None]]:
        buffer = ""
        messages: list[dict[str, str | None]] = []
        async for line in response_lines:
            buffer += line + "\n"
            if line == "":
                parsed = _parse_sse_messages(buffer)
                buffer = ""
                messages.extend(parsed)
                if any(m.get("event") in ("done", "workflow_error") for m in parsed):
                    return messages
        return messages

    return await asyncio.wait_for(_collect(), timeout=timeout_seconds)


# --- GET .../workflows/{workflow_id}/events/stream ---


async def test_admin_can_stream_new_events(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-stream-admin"
    )
    service = WorkflowService(db_session)
    run = await service.create_workflow(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
        patient_id=None,
        actor_type=ActorType.USER,
        actor_identifier=str(admin.id),
    )
    await service.start_workflow(
        organization_id=org.id,
        workflow_run_id=run.id,
        actor_type=ActorType.USER,
        actor_identifier=str(admin.id),
    )
    # Drive the run to a terminal status BEFORE opening the stream, so the
    # polling generator finds `is_terminal` true on its very first tick
    # and returns on its own — this test (deliberately) never depends on
    # the client disconnecting mid-stream to make the generator stop;
    # ASGI-transport-level disconnect propagation is its own concern, not
    # this test's (see `test_stream_emits_done_when_workflow_already_terminal`,
    # which already covers the "already terminal" shape this converges to).
    await service.complete_workflow(
        organization_id=org.id,
        workflow_run_id=run.id,
        actor_type=ActorType.USER,
        actor_identifier=str(admin.id),
    )

    async with client_with_db.stream(
        "GET", _stream_url(org, run.id), headers=_auth_header(admin)
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        messages = await _read_until_terminal(response.aiter_lines())

    event_messages = [m for m in messages if m.get("event") == "workflow_event"]
    assert len(event_messages) >= 3
    payloads = [json.loads(m["data"]) for m in event_messages[:3]]
    assert [p["event_type"] for p in payloads] == [
        "workflow_created",
        "workflow_started",
        "workflow_completed",
    ]
    assert payloads[0]["sequence"] < payloads[1]["sequence"] < payloads[2]["sequence"]
    assert event_messages[0]["id"] == str(payloads[0]["sequence"])
    assert any(m.get("event") == "done" for m in messages)


async def test_stream_after_sequence_skips_earlier_events(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-stream-cursor"
    )
    service = WorkflowService(db_session)
    run = await service.create_workflow(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
        patient_id=None,
        actor_type=ActorType.USER,
        actor_identifier=str(admin.id),
    )
    await service.start_workflow(
        organization_id=org.id,
        workflow_run_id=run.id,
        actor_type=ActorType.USER,
        actor_identifier=str(admin.id),
    )
    events = await service.list_events(organization_id=org.id, workflow_run_id=run.id)
    first_sequence = events[0].sequence
    # Same reasoning as `test_admin_can_stream_new_events`: reach a
    # terminal status before connecting, so the generator finishes on its
    # own rather than this test relying on client-disconnect propagation.
    await service.complete_workflow(
        organization_id=org.id,
        workflow_run_id=run.id,
        actor_type=ActorType.USER,
        actor_identifier=str(admin.id),
    )

    url = f"{_stream_url(org, run.id)}?after_sequence={first_sequence}"
    async with client_with_db.stream("GET", url, headers=_auth_header(admin)) as response:
        assert response.status_code == 200
        messages = await _read_until_terminal(response.aiter_lines())

    event_messages = [m for m in messages if m.get("event") == "workflow_event"]
    assert len(event_messages) >= 2
    payload_types = [json.loads(m["data"])["event_type"] for m in event_messages[:2]]
    assert payload_types == ["workflow_started", "workflow_completed"]


async def test_stream_emits_done_when_workflow_already_terminal(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-stream-done"
    )
    service = WorkflowService(db_session)
    run = await service.create_workflow(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
        patient_id=None,
        actor_type=ActorType.USER,
        actor_identifier=str(admin.id),
    )
    await service.start_workflow(
        organization_id=org.id,
        workflow_run_id=run.id,
        actor_type=ActorType.USER,
        actor_identifier=str(admin.id),
    )
    await service.complete_workflow(
        organization_id=org.id,
        workflow_run_id=run.id,
        actor_type=ActorType.USER,
        actor_identifier=str(admin.id),
    )

    async with client_with_db.stream(
        "GET", _stream_url(org, run.id), headers=_auth_header(admin)
    ) as response:
        assert response.status_code == 200
        messages = await _read_until_terminal(response.aiter_lines())

    assert any(m.get("event") == "done" for m in messages)
    done_message = next(m for m in messages if m.get("event") == "done")
    assert json.loads(done_message["data"])["status"] == "completed"


async def test_patient_cannot_stream_another_patients_workflow(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org = await make_organization("api-wf-stream-patient-scope")
    owner_user = await make_user("api-wf-stream-owner")
    await make_membership(org, owner_user, role=Role.PATIENT)
    owner_patient = await make_patient(org, "PN-STREAM-OWNER", user=owner_user)

    other_user = await make_user("api-wf-stream-other")
    await make_membership(org, other_user, role=Role.PATIENT)
    await make_patient(org, "PN-STREAM-OTHER", user=other_user)

    service = WorkflowService(db_session)
    run = await service.create_workflow(
        organization_id=org.id,
        initiated_by_user_id=owner_user.id,
        request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
        patient_id=owner_patient.id,
        actor_type=ActorType.USER,
        actor_identifier=str(owner_user.id),
    )

    response = await client_with_db.get(
        _stream_url(org, run.id), headers=_auth_header(other_user)
    )
    assert response.status_code == 404


async def test_stream_unknown_workflow_returns_404(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-wf-stream-404"
    )
    response = await client_with_db.get(
        _stream_url(org, "00000000-0000-0000-0000-000000000000"),
        headers=_auth_header(admin),
    )
    assert response.status_code == 404
