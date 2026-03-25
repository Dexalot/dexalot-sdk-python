"""Unit tests for observability module."""

import json
import logging
from io import StringIO

from dexalot_sdk.utils.observability import (
    JSONFormatter,
    configure_logging,
    get_logger,
    get_request_id,
    log_event,
    track_operation,
    with_request_id,
)


def test_json_formatter():
    """Test JSON formatter produces valid JSON."""
    # Create a string stream to capture output
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    logger = logging.getLogger("test_json_formatter")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Log a message with extra fields
    logger.info("test message", extra={"extra_fields": {"key": "value", "number": 42}})

    # Get output and parse JSON
    output = stream.getvalue().strip()
    data = json.loads(output)

    # Verify JSON structure
    assert data["level"] == "INFO"
    assert data["message"] == "test message"
    assert data["key"] == "value"
    assert data["number"] == 42
    assert "timestamp" in data
    assert "logger" in data


def test_configure_logging_console():
    """Test console logging configuration."""
    logger = configure_logging(log_level="DEBUG", log_format="console")

    assert logger.level == logging.DEBUG
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.StreamHandler)
    assert not isinstance(logger.handlers[0].formatter, JSONFormatter)


def test_configure_logging_json():
    """Test JSON logging configuration."""
    logger = configure_logging(log_level="INFO", log_format="json")

    assert logger.level == logging.INFO
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0].formatter, JSONFormatter)


def test_request_id_tracking():
    """Test request ID context manager."""
    assert get_request_id() is None

    with with_request_id("test-123"):
        assert get_request_id() == "test-123"

        # Nested context
        with with_request_id("test-456"):
            assert get_request_id() == "test-456"

        # Back to outer context
        assert get_request_id() == "test-123"

    # Context cleared
    assert get_request_id() is None


def test_request_id_auto_generation():
    """Test request ID auto-generation."""
    with with_request_id() as request_id:
        assert request_id is not None
        assert len(request_id) > 0
        assert get_request_id() == request_id


def test_track_operation_success(caplog):
    """Test operation tracking logs success."""
    logger = get_logger("test_track_operation")

    with caplog.at_level(logging.INFO, logger="test_track_operation"):
        with track_operation(logger, "test_op", foo="bar"):
            pass

    # Verify log message (extra fields are in the record but may not appear in console format)
    assert "test_op - completed in" in caplog.text


def test_track_operation_failure(caplog):
    """Test operation tracking logs failures."""
    logger = get_logger("test_track_failure")

    with caplog.at_level(logging.ERROR, logger="test_track_failure"):
        try:
            with track_operation(logger, "test_op"):
                raise ValueError("test error")
        except ValueError:
            pass

    # Verify error was logged
    assert "test_op failed" in caplog.text
    assert "test error" in caplog.text


def test_log_event(caplog):
    """Test structured event logging."""
    logger = get_logger("test_log_event")

    with caplog.at_level(logging.INFO, logger="test_log_event"):
        log_event(logger, "info", "order_submitted", pair="AVAX/USDC", side="BUY", amount=10.0)

    # Verify event was logged
    assert "Order Submitted" in caplog.text or "order_submitted" in caplog.text


def test_request_id_in_json_logs():
    """Test that request ID appears in JSON formatted logs."""
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    logger = logging.getLogger("test_request_id_json")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    with with_request_id("req-789"):
        logger.info("test message", extra={"extra_fields": {}})

    output = stream.getvalue().strip()
    data = json.loads(output)

    assert data["request_id"] == "req-789"


def test_get_logger():
    """Test logger retrieval."""
    logger = get_logger("dexalot_sdk.test")

    assert logger.name == "dexalot_sdk.test"
    assert isinstance(logger, logging.Logger)


def test_json_formatter_with_exception():
    """Test JSON formatter includes exception info."""
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    logger = logging.getLogger("test_json_exception")
    logger.addHandler(handler)
    logger.setLevel(logging.ERROR)
    logger.propagate = False

    try:
        raise ValueError("Test exception")
    except ValueError:
        logger.error("Error occurred", exc_info=True)

    output = stream.getvalue().strip()
    data = json.loads(output)

    assert "exception" in data
    assert "ValueError: Test exception" in data["exception"]


def test_clear_request_id():
    """Test clearing request ID."""
    from dexalot_sdk.utils.observability import clear_request_id, set_request_id

    set_request_id("test-123")
    assert get_request_id() == "test-123"

    clear_request_id()
    assert get_request_id() is None


def test_console_formatter_truncation():
    """Test ConsoleFormatter truncates long values."""
    import logging

    from dexalot_sdk.utils.observability import configure_logging

    logger = configure_logging(log_level="INFO", log_format="console")
    formatter = logger.handlers[0].formatter

    # Create a log record with a long value in extra_fields
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    record.extra_fields = {
        "function": "test_func",
        "operation": "test_op",
        "long_value": "a" * 100,  # 100 characters, should be truncated to 47 + "..."
        "short_value": "short",
    }

    formatted = formatter.format(record)
    # Should contain truncated value
    assert "long_value=" in formatted
    assert "a" * 47 + "..." in formatted
    assert "short_value=short" in formatted
