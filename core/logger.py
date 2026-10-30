import sys
from typing import Any

from loguru import logger

DEFAULT_LOG_DIR = "logs"
DEFAULT_RETENTION = "7 days"

STDERR_FORMAT = (
    "<white>{time:HH:mm:ss}</white> "
    "[<level>{level}</level>] "
    "<level>{message}</level>"
)
STDERR_START_LEVEL = "INFO"
STDERR_RUNTIME_LEVEL = "WARNING"

_STDERR_SINK_ID: int | None = None


def _add_stderr(level: str) -> None:
    global _STDERR_SINK_ID
    if _STDERR_SINK_ID is not None:
        try:
            logger.remove(_STDERR_SINK_ID)
        except ValueError:
            pass
    _STDERR_SINK_ID = logger.add(
        sys.stderr,
        format=STDERR_FORMAT,
        colorize=True,
        level=level,
    )


def mute_stderr_info() -> None:
    _add_stderr(STDERR_RUNTIME_LEVEL)


def setup_logger(config: dict[str, Any] | None = None) -> None:
    cfg = config or {}
    log_dir = str(cfg.get("log_dir") or DEFAULT_LOG_DIR)
    retention = str(cfg.get("log_retention") or DEFAULT_RETENTION)
    logger.remove()

    logger.level("INFO", color="<white>")
    logger.level("SUCCESS", color="<green>")
    logger.level("ERROR", color="<red>")
    logger.level("WARNING", color="<yellow>")

    _add_stderr(STDERR_START_LEVEL)

    file_format = "{time:YYYY-MM-DD HH:mm:ss} [{level}] {message}"

    logger.add(
        f"{log_dir}/info_{{time:YYYY-MM-DD}}.log",
        format=file_format,
        level="INFO",
        rotation="00:00",
        retention=retention,
        encoding="utf-8",
        colorize=False,
    )
    logger.add(
        f"{log_dir}/error_{{time:YYYY-MM-DD}}.log",
        format=file_format,
        level="ERROR",
        rotation="00:00",
        retention=retention,
        encoding="utf-8",
        colorize=False,
    )