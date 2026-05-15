"""Structured file logging with rotation."""

import os
import logging
import logging.handlers
from datetime import datetime, timezone


LOG_DIR = os.getenv("LOG_DIR", "logs").strip() or "logs"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "30") or "30")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024)) or str(10 * 1024 * 1024))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "10") or "10")


def setup_logging() -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)

    logger = logging.getLogger("pricer3d")
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    # Avoid duplicate handlers on reload
    if logger.handlers:
        return logger

    # File handler with rotation
    access_log = os.path.join(LOG_DIR, "access.log")
    access_handler = logging.handlers.RotatingFileHandler(
        access_log,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    access_handler.setLevel(logging.INFO)
    access_fmt = logging.Formatter(
        '%(asctime)s | %(levelname)-5s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    access_handler.setFormatter(access_fmt)

    # Error log separately
    error_log = os.path.join(LOG_DIR, "error.log")
    error_handler = logging.handlers.RotatingFileHandler(
        error_log,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.WARNING)
    error_fmt = logging.Formatter(
        '%(asctime)s | %(levelname)-5s | %(pathname)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    error_handler.setFormatter(error_fmt)

    # Console handler (stderr)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(error_fmt)

    logger.addHandler(access_handler)
    logger.addHandler(error_handler)
    logger.addHandler(console_handler)

    return logger


def log_request(logger: logging.Logger, method: str, path: str, status: int, duration_ms: float, client_ip: str, request_id: str = "-") -> None:
    """Log a single HTTP request in structured format."""
    logger.info(
        "ip=%s method=%s path=%s status=%d duration_ms=%.2f rid=%s",
        client_ip, method, path, status, duration_ms, request_id,
    )


def log_event(logger: logging.Logger, event: str, **kwargs) -> None:
    """Log a business event with structured key=value pairs."""
    parts = [f"event={event}"]
    for k, v in kwargs.items():
        parts.append(f"{k}={v}")
    logger.info(" ".join(parts))
