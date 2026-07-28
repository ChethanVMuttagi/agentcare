"""`FakeNotificationProvider`: a deterministic `NotificationProvider` for
tests — mirrors `app.ai.providers.fake_provider.FakeLLMProvider`'s
shape exactly.

Every worker/service/concurrency test in this codebase that exercises
reminder DELIVERY uses this instead of `ConsoleNotificationProvider`,
so a test can deterministically force a failure (to exercise retry/
exhaustion) or a success, and can assert exactly which messages were
"sent" without depending on log output.
"""

from __future__ import annotations

from app.notifications.base import NotificationMessage, NotificationResult

_PROVIDER_NAME = "fake"


class FakeNotificationProvider:
    """`always_succeed=True` (default): every `send()` call succeeds.
    `always_succeed=False`: every call fails with `fail_detail`. Every
    call is recorded in `.sent_messages`, in order, regardless of
    outcome — tests assert against this list rather than trusting a
    result code alone."""

    def __init__(
        self, *, always_succeed: bool = True, fail_detail: str = "simulated failure"
    ) -> None:
        self._always_succeed = always_succeed
        self._fail_detail = fail_detail
        self.sent_messages: list[NotificationMessage] = []

    @property
    def provider_name(self) -> str:
        return _PROVIDER_NAME

    async def send(self, message: NotificationMessage) -> NotificationResult:
        self.sent_messages.append(message)
        if self._always_succeed:
            return NotificationResult(success=True, provider_name=_PROVIDER_NAME)
        return NotificationResult(
            success=False, provider_name=_PROVIDER_NAME, safe_detail=self._fail_detail
        )


class AlwaysRaisingFakeNotificationProvider:
    """A `FakeNotificationProvider` variant dedicated to simulating a
    genuinely unexpected provider-side exception (not an ordinary
    delivery failure) — proves `ReminderWorker` treats a raised
    exception as a failed attempt too, never letting one bad reminder
    crash the poll loop. Kept separate for test readability, mirroring
    `app.ai.providers.fake_provider.AlwaysUnavailableFakeLLMProvider`."""

    @property
    def provider_name(self) -> str:
        return "always-raising-fake"

    async def send(self, message: NotificationMessage) -> NotificationResult:
        raise RuntimeError("simulated unexpected provider failure")
