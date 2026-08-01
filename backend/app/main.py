import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.ai.providers.errors import ProviderConfigurationError
from app.ai.providers.factory import close_llm_provider, get_llm_provider
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.rate_limit import limiter
from app.db.session import dispose_engine, get_sessionmaker
from app.notifications.console import ConsoleNotificationProvider
from app.workers.reminder_worker import ReminderWorker

logger = logging.getLogger("agentcare.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan.

    STORY-013: when `REMINDER_WORKER_ENABLED=true` (never true by
    default — see `app.core.config.Settings`), starts
    `ReminderWorker.run_forever()` as a background task, stopping it
    cleanly on shutdown via `asyncio.Event` (never `task.cancel()` mid-
    database-write — the worker's own poll loop checks the event between
    cycles). Disabled, this block never touches the database at all,
    matching every route that doesn't need one.

    Sprint 2: also opportunistically builds the shared `LLMProvider` (and
    its underlying HTTP client) once at startup via `get_llm_provider()`
    — see `app.ai.providers.factory` — so the first real request doesn't
    pay client-construction cost, and so exactly one client is ever
    created for the process lifetime rather than one per request. Left
    unbuilt (never fails startup) when LLM configuration is absent or
    incomplete, preserving the existing "the app starts either way; only
    `POST .../agent/execute` fails clearly if it's actually called"
    behavior — see docs/AI_SAFETY.md "Model/Provider Configuration".
    """
    settings = get_settings()
    stop_event = asyncio.Event()
    worker_task: asyncio.Task[None] | None = None

    if settings.reminder_worker_enabled:
        worker = ReminderWorker(
            get_sessionmaker(),
            ConsoleNotificationProvider(),
            batch_size=settings.reminder_worker_batch_size,
            poll_interval_seconds=settings.reminder_worker_poll_interval_seconds,
        )
        logger.info("starting reminder worker %s", worker.worker_id)
        worker_task = asyncio.create_task(worker.run_forever(stop_event=stop_event))

    try:
        get_llm_provider()
        logger.info("initialized LLM provider %r at startup", settings.llm_provider)
    except ProviderConfigurationError:
        logger.info(
            "LLM provider not configured at startup; POST .../agent/execute "
            "will fail clearly if called"
        )

    yield

    stop_event.set()
    if worker_task is not None:
        await worker_task
    await close_llm_provider()
    await dispose_engine()


def create_app() -> FastAPI:
    """Application factory: build a fully configured FastAPI instance."""
    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.state.limiter = limiter

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    register_exception_handlers(app)

    return app


app = create_app()
