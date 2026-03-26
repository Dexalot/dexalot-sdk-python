"""
Observability utilities for Dexalot SDK.

Provides lightweight structured logging using Python's standard library.
Supports JSON and console output formats, request ID tracking, and performance timing.
"""

import functools
import inspect
import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

# Thread-local storage for request ID
_thread_local = threading.local()


class JSONFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record as JSON.

        Args:
            record: Log record to format

        Returns:
            JSON string representation of log record
        """
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add extra fields from record
        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)

        # Add request ID if available
        request_id = getattr(_thread_local, "request_id", None)
        if request_id:
            log_data["request_id"] = request_id

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


class ConsoleFormatter(logging.Formatter):
    """Custom console formatter that includes context from extra_fields (excluding function/operation already in message)."""

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record for console output with enhanced context.

        Args:
            record: Log record to format

        Returns:
            Formatted log string
        """
        # Base format with timestamp, level, logger, and message
        base_msg = super().format(record)

        # Extract extra_fields if available
        extra_fields = getattr(record, "extra_fields", {})
        if not extra_fields:
            return base_msg

        # Build context string with meaningful fields (exclude function/operation already in message)
        context_parts = []

        # Add other useful context (exclude internal fields and function/operation)
        exclude_fields = {"operation", "function", "duration", "status", "error", "timestamp"}
        for key, value in extra_fields.items():
            if key not in exclude_fields and value is not None:
                # Truncate long values
                str_value = str(value)
                if len(str_value) > 50:
                    str_value = str_value[:47] + "..."
                context_parts.append(f"{key}={str_value}")

        # Append context if any
        if context_parts:
            return f"{base_msg} [{', '.join(context_parts)}]"

        return base_msg


def configure_logging(
    log_level: str | None = None,
    log_format: str = "console",
    logger_name: str = "dexalot_sdk",
) -> logging.Logger:
    """
    Configure SDK logging.

    Args:
        log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
                   Defaults to env var DEXALOT_LOG_LEVEL or INFO.
        log_format: Output format - "json" or "console".
                    Defaults to env var DEXALOT_LOG_FORMAT or "console".
        logger_name: Logger name (default: "dexalot_sdk")

    Returns:
        Configured logger instance

    Example:
        >>> configure_logging(log_level="DEBUG", log_format="json")
        >>> logger = logging.getLogger("dexalot_sdk")
    """
    # Get configuration from env vars with fallbacks
    level_str: str = log_level or os.getenv("DEXALOT_LOG_LEVEL") or "INFO"
    format_type: str = log_format or os.getenv("DEXALOT_LOG_FORMAT") or "console"

    # Convert level string to logging constant
    level = getattr(logging, level_str.upper(), logging.INFO)

    # Get or create logger
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    # Create console handler
    handler = logging.StreamHandler()
    handler.setLevel(level)

    # Set formatter based on format type
    formatter: logging.Formatter
    if format_type == "json":
        formatter = JSONFormatter()
    else:
        # Console format with timestamp and structured fields
        formatter = ConsoleFormatter(
            fmt="%(asctime)s [%(levelname)-8s] %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # Prevent propagation to root logger
    logger.propagate = False

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a specific module.

    Args:
        name: Module name (e.g., "dexalot_sdk.core.clob")

    Returns:
        Logger instance

    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("Operation completed")
    """
    return logging.getLogger(name)


def set_request_id(request_id: str | None) -> None:
    """
    Set request ID for current thread.

    Args:
        request_id: Request ID to set
    """
    _thread_local.request_id = request_id


def get_request_id() -> str | None:
    """
    Get request ID for current thread.

    Returns:
        Current request ID or None
    """
    return getattr(_thread_local, "request_id", None)


def clear_request_id() -> None:
    """Clear request ID for current thread."""
    _thread_local.request_id = None


@contextmanager
def with_request_id(request_id: str | None = None):
    """
    Context manager for request ID tracking.

    Args:
        request_id: Request ID to set. If None, generates a UUID.

    Yields:
        The request ID being used

    Example:
        >>> with with_request_id("req-123"):
        ...     client.get_tokens()  # All logs will include request_id
    """
    old_request_id = get_request_id()
    new_request_id = request_id or str(uuid.uuid4())

    try:
        set_request_id(new_request_id)
        yield new_request_id
    finally:
        set_request_id(old_request_id)


@contextmanager
def track_operation(logger: logging.Logger, operation: str, **extra_context):
    """
    Context manager for tracking operation timing and logging.

    Args:
        logger: Logger instance
        operation: Operation name
        **extra_context: Additional context fields to log

    Yields:
        None

    Example:
        >>> with track_operation(logger, "fetch_tokens", env="fuji"):
        ...     # ... operation code ...
        ...     pass
    """
    start_time = time.time()

    # Create LogRecord with extra fields
    extra_fields = {"operation": operation, **extra_context}

    try:
        yield
        duration = time.time() - start_time
        extra_fields["duration"] = round(duration, 3)
        extra_fields["status"] = "success"

        # Format duration: use milliseconds for very fast operations (< 1ms), otherwise seconds
        if duration < 0.001:
            duration_ms = duration * 1_000
            duration_str = f"{duration_ms:.3f}ms"
        else:
            duration_str = f"{duration:.3f}s"

        # Include function name in message if available and different from operation
        function_name = extra_fields.get("function", "")
        if function_name and function_name != operation:
            message = f"op={operation} - fn={function_name} - completed in {duration_str}"
        else:
            message = f"op={operation} - completed in {duration_str}"

        logger.info(message, extra={"extra_fields": extra_fields})
    except Exception as e:
        duration = time.time() - start_time
        extra_fields["duration"] = round(duration, 3)
        extra_fields["status"] = "failed"
        extra_fields["error"] = str(e)

        logger.error(
            f"{operation} failed after {duration:.3f}s: {e}",
            extra={"extra_fields": extra_fields},
            exc_info=True,
        )
        raise


def log_event(logger: logging.Logger, level: str, event: str, **context):
    """
    Log a structured event.

    Args:
        logger: Logger instance
        level: Log level (debug, info, warning, error, critical)
        event: Event name
        **context: Event context fields

    Example:
        >>> log_event(logger, "info", "order_submitted",
        ...           pair="AVAX/USDC", side="BUY", amount=10.0)
    """
    log_method = getattr(logger, level.lower())
    message = event.replace("_", " ").title()

    log_method(message, extra={"extra_fields": {"event": event, **context}})


def track_method(operation: str, **extra_context):
    """
    Decorator for tracking method execution.
    Assumes the first argument is 'self' and it has a 'logger' attribute.
    Falls back to module logger if 'self.logger' is missing.
    Automatically adds 'function' name to the log context.
    """

    def decorator(func):
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(self, *args, **kwargs):
                logger = getattr(self, "logger", logging.getLogger(func.__module__))
                context = {"function": func.__name__, **extra_context}
                with track_operation(logger, operation, **context):
                    return await func(self, *args, **kwargs)

            return async_wrapper
        else:

            @functools.wraps(func)
            def sync_wrapper(self, *args, **kwargs):
                logger = getattr(self, "logger", logging.getLogger(func.__module__))
                # Auto-inject function name into context
                context = {"function": func.__name__, **extra_context}
                with track_operation(logger, operation, **context):
                    return func(self, *args, **kwargs)

            return sync_wrapper

    return decorator
