import sys
from typing import Any

from loguru import logger

DEFAULT_LOG_DIR = "logs"
DEFAULT_RETENTION = "7 days"


def setup_logger(config: dict[str, Any] | None = None) -> None:
    cfg = config or {}
    log_dir = str(cfg.get("log_dir") or DEFAULT_LOG_DIR)
    retention = str(cfg.get("log_retention") or DEFAULT_RETENTION)
    logger.remove()

    file_format = "{time:YYYY-MM-DD HH:mm:ss} [{level}] {message}"

    logger.add(
        sys.stderr,
        format="<white>{time:HH:mm:ss}</white> [<level>{level}</level>] <level>{message}</level>",
        colorize=sys.stderr.isatty(),
        level="INFO",
    )
    logger.add(
        f"{log_dir}/info_{{time:YYYY-MM-DD}}.log",
        format=file_format,
        level="DEBUG",
        rotation="00:00",
        retention=retention,
        encoding="utf-8",
    )
    logger.add(
        f"{log_dir}/error_{{time:YYYY-MM-DD}}.log",
        format=file_format,
        level="ERROR",
        rotation="00:00",
        retention=retention,
        encoding="utf-8",
    )