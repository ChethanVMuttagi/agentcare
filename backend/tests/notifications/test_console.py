"""`app.notifications.console` / `app.notifications.fake` tests — no
database, no network."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.models.reminder import ReminderType
from app.notifications.base import NotificationMessage
from app.notifications.console import ConsoleNotificationProvider
from app.notifications.fake import AlwaysRaisingFakeNotificationProvider, FakeNotificationProvider

_MESSAGE = NotificationMessage(
    reminder_id=uuid.uuid4(),
    organization_id=uuid.uuid4(),
    patient_id=uuid.uuid4(),
    appointment_id=uuid.uuid4(),
    reminder_type=ReminderType.APPOINTMENT_REMINDER,
    appointment_start_at=datetime.now(UTC),
)


async def test_console_provider_always_succeeds() -> None:
    provider = ConsoleNotificationProvider()
    result = await provider.send(_MESSAGE)
    assert result.success is True
    assert result.provider_name == "console"


async def test_console_provider_name_property() -> None:
    provider = ConsoleNotificationProvider()
    assert provider.provider_name == "console"


async def test_fake_provider_defaults_to_success() -> None:
    provider = FakeNotificationProvider()
    result = await provider.send(_MESSAGE)
    assert result.success is True
    assert provider.sent_messages == [_MESSAGE]


async def test_fake_provider_can_be_configured_to_fail() -> None:
    provider = FakeNotificationProvider(always_succeed=False, fail_detail="simulated outage")
    result = await provider.send(_MESSAGE)
    assert result.success is False
    assert result.safe_detail == "simulated outage"
    assert provider.sent_messages == [_MESSAGE]


async def test_fake_provider_records_every_call_regardless_of_outcome() -> None:
    provider = FakeNotificationProvider(always_succeed=False)
    await provider.send(_MESSAGE)
    await provider.send(_MESSAGE)
    assert len(provider.sent_messages) == 2


async def test_always_raising_fake_provider_raises() -> None:
    provider = AlwaysRaisingFakeNotificationProvider()
    with pytest.raises(RuntimeError):
        await provider.send(_MESSAGE)
