"""Логирование в файл.

Собранный exe работает без консоли, поэтому sys.stderr недоступен:
все ошибки (в т.ч. необработанные исключения Tkinter) пишем в файл.
"""
import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler

from .config import log_path

LOGGER_NAME = "indexnow"
MAX_LOG_BYTES = 256 * 1024
LOG_BACKUP_COUNT = 2

_logger: logging.Logger | None = None


def get_logger() -> logging.Logger:
    """Логгер приложения с записью в файл (настраивается один раз)."""
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        handler: logging.Handler = RotatingFileHandler(
            log_path(),
            maxBytes=MAX_LOG_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    except OSError:
        logger.addHandler(logging.NullHandler())

    _logger = logger
    return logger


def log_exception(exc_type, exc, tb) -> None:
    """Единая точка для sys.excepthook и Tk report_callback_exception."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    get_logger().error(
        "Необработанное исключение:\n%s",
        "".join(traceback.format_exception(exc_type, exc, tb)),
    )


def install_global_handlers() -> None:
    """Ловит исключения вне Tk-колбэков (главный поток, потоки без своего)."""
    sys.excepthook = log_exception
