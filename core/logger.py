import sys

from loguru import logger

LOG_DIR = "logs"
RETENTION = "7 days"


def setup_logger() -> None:
    logger.remove()

    logger.level("INFO", color="<white>")
    logger.level("SUCCESS", color="<green>")
    logger.level("ERROR", color="<red>")
    logger.level("WARNING", color="<yellow>")

    log_format = (
        "<white>{time:HH:mm:ss}</white> "
        "[<level>{level}</level>] "
        "<level>{message}</level>"
    )

    logger.add(
        sys.stderr,
        format=log_format,
        colorize=True,
    )

    file_format = "{time:YYYY-MM-DD HH:mm:ss} [{level}] {message}"

    logger.add(
        f"{LOG_DIR}/info_{{time:YYYY-MM-DD}}.log",
        format=file_format,
        level="INFO",
        rotation="00:00",
        retention=RETENTION,
        encoding="utf-8",
        colorize=False,
    )
    logger.add(
        f"{LOG_DIR}/error_{{time:YYYY-MM-DD}}.log",
        format=file_format,
        level="ERROR",
        rotation="00:00",
        retention=RETENTION,
        encoding="utf-8",
        colorize=False,
    )