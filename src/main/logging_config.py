"""Logging setup for the migration tooling.

Every module logs through `logging.getLogger(__name__)` and configures nothing
on its own. Entry-point scripts call `configure_logging()` once so those
records reach the console (and optionally a file).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

DEFAULT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(
    level: int = logging.INFO,
    log_file: Path | str | None = None,
) -> None:
    """Configure the root logger.

    Args:
        level: minimum level to emit. `logging.DEBUG` adds the per-request and
            per-helper traces the modules already produce.
        log_file: optional file to mirror the records into; parent folders are
            created when missing.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))

    logging.basicConfig(
        level=level,
        format=DEFAULT_FORMAT,
        datefmt=DEFAULT_DATE_FORMAT,
        handlers=handlers,
        force=True,
    )
    logging.getLogger(__name__).debug(
        "Logging configured at level %s (file: %s)", logging.getLevelName(level), log_file
    )
