"""Structured logging configuration.

Rule: never log secrets or sensitive data. This includes API keys,
authorization headers, passwords, JWT secrets, database credentials,
patient data, and uploaded document contents. Application code must not
pass these values to log calls, even at DEBUG level. Secret-bearing
Settings fields use ``SecretStr`` specifically so that logging a settings
object (accidentally or otherwise) does not leak its value.
"""

from __future__ import annotations

import logging
import sys

from app.core.config import Settings

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(settings: Settings) -> None:
    """Configure root logging once, at application startup."""
    level = logging.DEBUG if settings.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        stream=sys.stdout,
        force=True,
    )
